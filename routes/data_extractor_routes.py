from flask import Blueprint, request, jsonify, session, make_response
import PyPDF2
from concurrent.futures import ThreadPoolExecutor
import logging
from firebase_admin import db
import uuid
from datetime import datetime
import re
from tools.file_extractor_tools import *
# Create a Blueprint for the file extractor module
file_extractor = Blueprint('extractor', __name__)

# Initialize an executor for async processing
executor = ThreadPoolExecutor()
def sanitize_filename(filename):
    """
    Replace illegal characters in a filename to make it Firebase-compatible.
    """
    return re.sub(r'[./#$\[\]]', '_', filename)


@file_extractor.route('/extractor/extract-data', methods=['POST'])
def extract_data():
    if 'files' not in request.files:
        return jsonify({'error': 'No files uploaded'}), 400

    uploaded_files = request.files.getlist('files')
    if not uploaded_files:
        return jsonify({'error': 'No files provided'}), 400

    # Retrieve the user ID (from session, token, or request headers)
    firebase_user = session.get('user')  # Example: Assume user info is in session
    # if not firebase_user:
    #     return jsonify({'error': 'User not authenticated'}), 401
    user_id = None
    if firebase_user:
        user_id = firebase_user.get('userId')
    # if not user_id:
    #     return jsonify({'error': 'User ID not found'}), 400

    # Generate a unique group ID (e.g., UUID or timestamp)
    group_id = datetime.now().strftime('%Y%m%d%H%M%S')  # Example: "20241222084530"

    results = {}

    def process_file(file):
        # Simulated function for extracting text and processing
        text = extract_text_from_pdf(file)
        if not text:
            return [], file.filename
        json_content = process_text_with_gpt(text)
        if not json_content:
            return [], file.filename
        data = parse_json_to_dataframe(json_content).to_dict(orient='records')

        # Sanitize the file name
        sanitized_filename = sanitize_filename(file.filename)

        # Save to Firebase under the user's uploads
        if firebase_user and user_id:
            firebase_ref = db.reference(f'uploads/{user_id}/{group_id}/{sanitized_filename}')
            firebase_ref.set(data)

        return data, file.filename

    # Process files asynchronously
    futures = [executor.submit(process_file, file) for file in uploaded_files]
    for future in futures:
        data, filename = future.result()
        results[filename] = data

    # Save metadata for the group under the user
    metadata = {
            'upload_time': datetime.now().isoformat(),
            'file_count': len(uploaded_files),
            'files': [sanitize_filename(file.filename) for file in uploaded_files]
        }
    if firebase_user and user_id:
        group_metadata_ref = db.reference(f'uploads/{user_id}/{group_id}/metadata')
        group_metadata_ref.set(metadata)

    return jsonify({'group_id': group_id, 'files': results, 'upload_time': datetime.now().isoformat(),
            'file_count': len(uploaded_files),})


@file_extractor.route('/extractor/get-user-data', methods=['GET'])
def get_user_data():
    # Retrieve the user ID (from session, token, or request headers)
    firebase_user = session.get('user')  # Example: Assume user info is in session
    if not firebase_user:
        return jsonify({'error': 'User not authenticated', 'uploads': []}), 200

    user_id = firebase_user.get('userId')
    if not user_id:
        return jsonify({'error': 'User ID not found'}), 400

    try:
        # Reference the user's uploads in Firebase
        user_uploads_ref = db.reference(f'uploads/{user_id}')
        user_uploads = user_uploads_ref.get()  # Fetch all data for the user

        if not user_uploads:
            return jsonify({'message': 'No uploads found for the current user.', 'uploads': []}), 200

        # Parse the data
        uploads = []
        for group_id, group_data in user_uploads.items():
            metadata = group_data.get('metadata', {})
            files_data = {
                file_name: group_data[file_name]
                for file_name in group_data.keys()
                if file_name != 'metadata'  # Exclude metadata from the file contents
            }

            uploads.append({
                'group_id': group_id,
                'upload_time': metadata.get('upload_time', 'Unknown'),
                'file_count': metadata.get('file_count', 0),
                'files': files_data,  # Include file contents here
            })

        # Sort uploads by `upload_time` (descending order)
        sorted_uploads = sorted(uploads, key=lambda x: x['upload_time'], reverse=True)

        return jsonify({'uploads': sorted_uploads}), 200

    except Exception as e:
        print(f"Error fetching user data: {str(e)}")
        return jsonify({'error': 'Failed to fetch user data.'}), 500

@file_extractor.route('/extractor/delete-group/<group_id>', methods=['DELETE'])
def delete_group(group_id):
    """
    Delete a group of uploads by group_id for the authenticated user.
    """
    # Retrieve the user ID (from session, token, or request headers)
    firebase_user = session.get('user')  # Example: Assume user info is in session
    if not firebase_user:
        return jsonify({'error': 'User not authenticated'}), 401

    user_id = firebase_user.get('userId')
    if not user_id:
        return jsonify({'error': 'User ID not found'}), 400

    try:
        # Reference the user's uploads in Firebase
        group_ref = db.reference(f'uploads/{user_id}/{group_id}')
        group_data = group_ref.get()

        if not group_data:
            return jsonify({'message': f'No group found with group_id: {group_id}'}), 404

        # Delete the group
        group_ref.delete()

        return jsonify({'message': f'Group {group_id} deleted successfully.'}), 200

    except Exception as e:
        logging.error(f"Error deleting group {group_id}: {str(e)}")
        return jsonify({'error': 'Failed to delete the group.'}), 500
