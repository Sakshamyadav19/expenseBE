import os
from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials,firestore, exceptions
from flask_cors import CORS
from splitwise import Splitwise
from dotenv import load_dotenv

# Load credentials from environment variables
load_dotenv(dotenv_path=".env")
FIREBASE_CREDENTIALS_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH")
SPLITWISE_CONSUMER_KEY = os.getenv("SPLITWISE_CONSUMER_KEY")
SPLITWISE_CONSUMER_SECRET = os.getenv("SPLITWISE_CONSUMER_SECRET")



print(FIREBASE_CREDENTIALS_PATH, SPLITWISE_CONSUMER_KEY, SPLITWISE_CONSUMER_SECRET)
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
        expense_data = {
            'email': email,
            'title': title,
            'amount': amount,
            'category': category
        }
        
        expense_ref = db.collection('expenses').add(expense_data)
        
        # Access the ID of the added document
        expense_id = expense_ref[1].id
        
        return jsonify({'message': 'Expense added successfully', 'id': expense_id}), 200
    
    except exceptions.FirebaseError as e:
        return jsonify({'error': f'Failed to add expense: {str(e)}'}), 500
    


@app.route('/get-expenses', methods=['GET'])
def get_expense():
    # Retrieve email from URL parameters
    email = request.args.get('email')
    
    if not email:
        return jsonify({"error": "Email parameter is required"}), 400
    
    try:

        # Reference the collection where expenses are stored
        expenses_ref = db.collection('expenses')
        # Query Firestore for documents where 'email' matches the provided email

        query = expenses_ref.where("email", "==", email).stream()

        # Prepare a list to hold retrieved records
        expenses = []
        for doc in query:
            print(doc)
            expense = doc.to_dict()
            expense['id'] = doc.id  
            expenses.append(expense)
        
        return jsonify(expenses), 200
    
    except Exception as e:
        print(e)
        return jsonify({"error": str(e)}), 500

# Endpoint to initiate Splitwise OAuth flow
@app.route('/authsplitwise', methods=['GET'])
def initiate_splitwise_oauth():
    # Get the redirect_uri from the query parameters
    redirect_uri = request.args.get('redirect_uri')
    if not redirect_uri:
        return jsonify({"error": "Missing redirect_uri parameter"}), 400

    # Generate the OAuth URL and state
    url, state = s.getOAuth2AuthorizeURL(redirect_uri)

    # Optionally, store the state for later verification
    return jsonify({"url": url, "state": state})

@app.route('/callback', methods=['GET'])
def handle_splitwise_callback():
    # Get the code and state from the query parameters
    code = request.args.get('code')
    state = request.args.get('state')

    if not code or not state:
        return jsonify({"error": "Missing code or state parameter"}), 400

    try:
        token_info = s.getOAuth2AccessToken(code, 'exp://10.0.0.240:8081')

        # Print token_info to debug its structure
        print("Token Info:", token_info)
        print("Type of token_info:", type(token_info))

        # Ensure token_info is a dictionary
        if isinstance(token_info, str):
            return jsonify({"error": "Invalid response from Splitwise, expected a dictionary"}), 500

        access_token = token_info.get('access_token')


        s.setOAuth2AccessToken(token_info)
        expenses = s.getExpenses(offset=2)

        for expense in expenses:
            print(expense.getDetails(), expense.cost)

        return jsonify({"access_token": access_token}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
