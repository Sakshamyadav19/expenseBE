import os
from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials,firestore, exceptions
from flask_cors import CORS
from splitwise import Splitwise
from dotenv import load_dotenv
import requests

# Load credentials from environment variables
load_dotenv(dotenv_path=".env")
FIREBASE_CREDENTIALS_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH")
SPLITWISE_CONSUMER_KEY = os.getenv("SPLITWISE_CONSUMER_KEY")
SPLITWISE_CONSUMER_SECRET = os.getenv("SPLITWISE_CONSUMER_SECRET")
SPLITWISE_TOKEN = os.getenv("SPLITWISE_TOKEN")


cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
firebase_admin.initialize_app(cred)
s = Splitwise(SPLITWISE_CONSUMER_KEY, SPLITWISE_CONSUMER_SECRET)

try:
    db = firestore.client()
except Exception as e:
    print(f"Error initializing Firestore client: {e}")

app = Flask(__name__)
CORS(app)

# POST endpoint to add an expense
@app.route('/add-expense', methods=['POST'])
def add_expense():
    data = request.get_json()
    
    # Validate input
    if not data or 'email' not in data or 'title' not in data or 'amount' not in data or 'category' not in data:
        return jsonify({'error': 'Missing required fields: email, title, amount, category'}), 400
    
    email = data['email']
    title = data['title']
    amount = data['amount']
    category = data['category']
    
    # Validate email format
    if '@' not in email:
        return jsonify({'error': 'Invalid email format'}), 400
    
    # Validate amount (should be a positive number)
    if amount <= 0:
        return jsonify({'error': 'Amount must be greater than 0'}), 400
    
    try:
        # Create a new expense document in Firestore with email at the start
        expense_item = {
            'title': title,
            'amount': amount,
            'category': category
        }
        
        user_ref = db.collection('users').document(email)
        doc = user_ref.get()
        if doc.exists:
            # Append the new expense to the existing expenses array
            user_ref.update({"expenses": firestore.ArrayUnion([expense_item])})
        else:
            # Create new document with expenses array and an empty access token
            user_ref.set({"expenses": [expense_item], "access_token": ""})
        
        return jsonify({'message': 'Expense added successfully'}), 200
    
    except exceptions.FirebaseError as e:
        return jsonify({'error': f'Failed to add expense: {str(e)}'}), 500
    


@app.route('/get-expenses', methods=['GET'])
def get_expense():
    # Retrieve email from URL parameters
    email = request.args.get('email')
    if not email:
        return jsonify({"error": "Email parameter is required"}), 400

    try:
        user_doc = db.collection('users').document(email).get()
        if user_doc.exists:
            data = user_doc.to_dict()
            # Get stored expenses from Firestore
            expenses = data.get("expenses", [])
            access_token = data.get("access_token", "")


            # If access_token exists, call Splitwise's /get_friends endpoint to fetch balances
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
                    overall_balance = 0.0
                    # Loop over each friend and add up every balance amount
                    for friend in friend_data.get("friends", []):
                        for balance_entry in friend.get("balance", []):
                            try:
                                overall_balance += float(balance_entry.get("amount", "0"))
                            except Exception as e:
                                print(f"Error parsing balance for friend {friend.get('id')}: {e}")
                    # Create an expense item representing the overall Splitwise balance
                    splitwise_expense = {
                        "title": "Splitwise",
                        "category": "others",
                        "amount": overall_balance
                    }
                    # Append this Splitwise balance expense to the stored expenses
                    expenses.append(splitwise_expense)
                else:
                    print("Failed to fetch friend balances. Status Code:", response.status_code)

            return jsonify(expenses), 200
        else:
            # If user document doesn't exist, return an empty list and no balance
            return jsonify([]), 200

    except Exception as e:
        print(e)
        return jsonify({"error": str(e)}), 500

# Endpoint to initiate Splitwise OAuth flow
@app.route('/authsplitwise', methods=['GET'])
def initiate_splitwise_oauth():
    # 1. Get the redirect_uri from the query parameters
    redirect_uri = request.args.get('redirect_uri')
    if not redirect_uri:
        return jsonify({"error": "Missing redirect_uri parameter"}), 400

    # 2. Generate the OAuth URL (this includes `state`)
    url, state = s.getOAuth2AuthorizeURL(redirect_uri)

    # 3. Return the URL and state to the client
    return jsonify({"url": url, "state": state})

@app.route('/callback', methods=['GET'])
def handle_splitwise_callback():
    """
    The mobile app calls this endpoint AFTER receiving ?code=xxx&state=yyy
    in the deep link. This route exchanges the code for an access token and
    stores it in Firestore. If no user document exists, it creates one.
    """
    code = request.args.get('code')
    state = request.args.get('state')
    email = request.args.get('email')
    
    if not code or not state or not email:
        return jsonify({"error": "Missing code, state, or email parameter"}), 400

    try:
        # Exchange code for access token (ensure redirect URI matches the one used during initiation)
        token_info = s.getOAuth2AccessToken(code, "exp://10.0.0.173:8081")
        
        # Validate token_info response
        if not isinstance(token_info, dict):
            return jsonify({"error": "Invalid response from Splitwise"}), 500
        
        access_token = token_info.get('access_token')
        if not access_token:
            return jsonify({"error": "No access token returned"}), 500

        # Optionally, set it on the Splitwise client for further calls
        s.setOAuth2AccessToken(token_info)

        # Store the access token in Firestore, creating a new user document if needed
        user_ref = db.collection('users').document(email)
        doc = user_ref.get()
        if doc.exists:
            user_ref.update({"access_token": access_token})
        else:
            user_ref.set({"access_token": access_token, "expenses": []})
        
        return jsonify({"access_token": access_token}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500



if __name__ == '__main__':
    # For local dev, run on your IP, e.g. "10.0.0.112"
    app.run(host='0.0.0.0', port=5001, debug=True)



@app.route('/getuserdetails', methods=['GET'])
def get_user_details():
    # Get the access token and state from the query parameters
    access_token = request.args.get('access_token')
    state = request.args.get('state')
    
    if not access_token or not state:
        return jsonify({"error": "Missing access_token or state parameter"}), 400

    # Verify the state (if you stored it earlier)
    if state != 'expected_state':
        return jsonify({"error": "Invalid state"}), 400

    # Use the access token to get user details
    try:
        user_details = s.getUserDetails(access_token)
        return jsonify(user_details)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
