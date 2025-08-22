import os
from flask import Flask, request, jsonify, redirect
import firebase_admin
from firebase_admin import credentials, firestore, exceptions
from flask_cors import CORS
from splitwise import Splitwise
from dotenv import load_dotenv
import requests

# Load credentials from environment variables
load_dotenv(dotenv_path=".env")
FIREBASE_CREDENTIALS_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH")
SPLITWISE_CONSUMER_KEY = os.getenv("SPLITWISE_CONSUMER_KEY")
SPLITWISE_CONSUMER_SECRET = os.getenv("SPLITWISE_CONSUMER_SECRET")
WEB_CLIENT_ID = os.getenv("WEB_CLIENT_ID")
WEB_CLIENT_SECRET = os.getenv("WEB_CLIENT_SECRET")
EXPO_DEV_IP = os.getenv("EXPO_DEV_IP")
EXPO_DEV_PORT = "8081" 

cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
firebase_admin.initialize_app(cred)
s = Splitwise(SPLITWISE_CONSUMER_KEY, SPLITWISE_CONSUMER_SECRET)

try:
    db = firestore.client()
except Exception as e:
    print(f"Error initializing Firestore client: {e}")

app = Flask(__name__)
CORS(app)

@app.route('/add-expense', methods=['POST'])
def add_expense():
    data = request.get_json()
    required_fields = ['email', 'title', 'amount', 'category']
    if not data or not all(field in data for field in required_fields):
        return jsonify({'error': 'Missing required fields: email, title, amount, category'}), 400

    email = data['email']
    title = data['title']
    amount = data['amount']
    category = data['category']

    if '@' not in email:
        return jsonify({'error': 'Invalid email format'}), 400
    if amount <= 0:
        return jsonify({'error': 'Amount must be greater than 0'}), 400

    try:
        expense_item = {'title': title, 'amount': amount, 'category': category}
        user_ref = db.collection('users').document(email)
        doc = user_ref.get()
        if doc.exists:
            user_ref.update({"expenses": firestore.ArrayUnion([expense_item])})
        else:
            user_ref.set({"expenses": [expense_item], "access_token": ""})

        return jsonify({'message': 'Expense added successfully'}), 200

    except exceptions.FirebaseError as e:
        return jsonify({'error': f'Failed to add expense: {str(e)}'}), 500

@app.route('/get-expenses', methods=['GET'])
def get_expense():
    email = request.args.get('email')
    if not email:
        return jsonify({"error": "Email parameter is required"}), 400

    try:
        user_doc = db.collection('users').document(email).get()
        if user_doc.exists:
            data = user_doc.to_dict()
            expenses = data.get("expenses", [])
            access_token = data.get("access_token", "")

            if access_token:
                response = requests.get(
                    "https://secure.splitwise.com/api/v3.0/get_friends",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json"
                    }
                )
                if response.status_code == 200:
                    friend_data = response.json()
                    overall_balance = sum(
                        float(entry.get("amount", 0))
                        for friend in friend_data.get("friends", [])
                        for entry in friend.get("balance", [])
                    )
                    expenses.append({
                        "title": "Splitwise",
                        "category": "others",
                        "amount": overall_balance
                    })
                else:
                    print("Failed to fetch balances:", response.status_code)

            return jsonify(expenses), 200
        return jsonify([]), 200

    except Exception as e:
        print(e)
        return jsonify({"error": str(e)}), 500

@app.route('/authsplitwise', methods=['GET'])
def initiate_splitwise_oauth():
    redirect_uri = request.args.get('redirect_uri')
    if not redirect_uri:
        return jsonify({"error": "Missing redirect_uri parameter"}), 400
    url, state = s.getOAuth2AuthorizeURL(redirect_uri)
    return jsonify({"url": url, "state": state})

@app.route('/callback', methods=['GET'])
def handle_splitwise_callback():
    code = request.args.get('code')
    state = request.args.get('state')
    email = request.args.get('email')

    if not code or not state or not email:
        return jsonify({"error": "Missing code, state, or email parameter"}), 400

    try:
        token_info = s.getOAuth2AccessToken(code, "exp://10.0.0.173:8081")
        access_token = token_info.get('access_token')
        if not access_token:
            return jsonify({"error": "No access token returned"}), 500

        s.setOAuth2AccessToken(token_info)
        user_ref = db.collection('users').document(email)
        doc = user_ref.get()
        if doc.exists:
            user_ref.update({"access_token": access_token})
        else:
            user_ref.set({"access_token": access_token, "expenses": []})

        return jsonify({"access_token": access_token}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/getuserdetails', methods=['GET'])
def get_user_details():
    access_token = request.args.get('access_token')
    state = request.args.get('state')

    if not access_token or not state:
        return jsonify({"error": "Missing access_token or state parameter"}), 400
    if state != 'expected_state':
        return jsonify({"error": "Invalid state"}), 400

    try:
        user_details = s.getUserDetails(access_token)
        return jsonify(user_details)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/google-oauth-callback', methods=['GET'])
def google_oauth_callback():
    """
    Handles the Google OAuth callback and redirects directly to the app.
    """
    code = request.args.get('code')
    state = request.args.get('state')
    error = request.args.get('error')
    
    # Base URL for Expo development client
    expo_base_url = f"exp+soothly://expo-development-client/?url=http%3A%2F%2F{EXPO_DEV_IP}%3A{EXPO_DEV_PORT}"
    
    # Handle OAuth errors
    if error:
        print(f"OAuth error: {error}")
        app_redirect_url = f"{expo_base_url}?error={error}"
        return redirect(app_redirect_url, code=302)
    
    if not code:
        print("No authorization code received")
        app_redirect_url = f"{expo_base_url}?error=no_code"
        return redirect(app_redirect_url, code=302)
    
    # Build redirect URL with code and state
    app_redirect_url = f"{expo_base_url}?code={code}"
    if state:
        app_redirect_url += f"&state={state}"
    
    print(f"Redirecting to app: {app_redirect_url}")
    
    # Direct redirect to mobile app
    return redirect(app_redirect_url, code=302)

@app.route('/exchange-code', methods=['POST'])
def exchange_code():
    """
    Exchanges authorization code for access token using PKCE.
    Called directly by the mobile app.
    """
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "No JSON data provided"}), 400
    
    code = data.get('code')
    code_verifier = data.get('code_verifier')
    
    if not code:
        return jsonify({"error": "No authorization code provided"}), 400
    
    if not code_verifier:
        return jsonify({"error": "No code verifier provided"}), 400
    
    try:
        # Exchange code for access token using Web OAuth client
        # Note: Web clients require both client_id and client_secret
        token_response = requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'code': code,
                'client_id': WEB_CLIENT_ID,  # Use web client for backend redirect
                'client_secret': WEB_CLIENT_SECRET,  # Use web client secret for backend redirect
                'redirect_uri': 'https://expensebe.onrender.com/google-oauth-callback',  # Web redirect URI for auth flow
                'grant_type': 'authorization_code',
                'code_verifier': code_verifier  # PKCE verifier
            },
            headers={
                'Content-Type': 'application/x-www-form-urlencoded'
            }
        )
        
        if token_response.status_code != 200:
            print(f"Token exchange failed: {token_response.text}")
            return jsonify({"error": "Failed to exchange code for token", "details": token_response.text}), 500
        
        token_data = token_response.json()
        access_token = token_data.get('access_token')
        refresh_token = token_data.get('refresh_token')
        expires_in = token_data.get('expires_in')
        
        if not access_token:
            return jsonify({"error": "No access token in response"}), 500
        
        return jsonify({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": expires_in,
            "token_type": token_data.get('token_type', 'Bearer')
        })
        
    except Exception as e:
        print(f"Error in code exchange: {str(e)}")
        return jsonify({"error": str(e)}), 500 

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
