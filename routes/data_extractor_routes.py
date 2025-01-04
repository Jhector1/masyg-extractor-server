from flask import Blueprint, request, jsonify, session, make_response
import PyPDF2
from concurrent.futures import ThreadPoolExecutor
import logging
from firebase_admin import db
import uuid
from datetime import datetime
import re
from services.file_extractor_service import *
# Create a Blueprint for the file extractor module
file_extractor = Blueprint('extractor', __name__)

# Initialize an executor for async processing
# executor = ThreadPoolExecutor()
def sanitize_filename(filename):
    """
    Replace illegal characters in a filename to make it Firebase-compatible.
    """
    return re.sub(r'[./#$\[\]]', '_', filename)






# Assume these helpers are imported:
# from your_module import extract_text_from_pdf, process_text_with_gpt, parse_json_to_dataframe, sanitize_filename
# from firebase_admin import db

# Create a global or app-level ThreadPoolExecutor with enough workers
executor = ThreadPoolExecutor(max_workers=5)  # Adjust as needed

@file_extractor.route('/extractor/extract-data', methods=['POST'])
def extract_data():
    """Extract data from multiple PDF files using GPT, store in Firebase, and return metadata."""
    # --- 1) Validate Request ---
    if 'files' not in request.files:
        return jsonify({'error': 'No files uploaded'}), 400
    uploaded_files = request.files.getlist('files')
    if not uploaded_files:
        return jsonify({'error': 'No files provided'}), 400

    # --- 2) Validate User ---
    firebase_user = session.get('user')  # Suppose user info is stored in session
    if not firebase_user:
        return jsonify({'error': 'User not authenticated'}), 401
    user_id = firebase_user.get('userId')
    if not user_id:
        return jsonify({'error': 'User ID not found'}), 400

    # --- 3) Generate Unique Group ID ---
    group_id = datetime.now().strftime('%Y%m%d%H%M%S')  # e.g., "20250103123045"

    results = {}  # Will hold file_name -> extracted data

    def process_file(pdf_file):
        """Extract text, run GPT, parse JSON, store in Firebase, return result."""
        # 3A) Extract Text
        text = extract_text_from_pdf(pdf_file)
        if not text:
            # If no text, return empty list
            return [], pdf_file.filename

        # 3B) Run GPT (could be truncated if very large)
        json_content = process_text_with_gpt(text)
        if not json_content:
            return [], pdf_file.filename

        # 3C) Parse JSON into records
        records = parse_json_to_dataframe(json_content).to_dict(orient='records')

        # 3D) Store in Firebase
        sanitized = sanitize_filename(pdf_file.filename)
        db.reference(f'uploads/{user_id}/{group_id}/{sanitized}').set(records)

        return records, pdf_file.filename

    # --- 4) Parallel File Processing ---
    futures = [executor.submit(process_file, f) for f in uploaded_files]
    for future in futures:
        data, filename = future.result()
        results[filename] = data

    # --- 5) Store Group Metadata ---
    metadata = {
        'upload_time': datetime.now().isoformat(),
        'file_count': len(uploaded_files),
        'files': [sanitize_filename(f.filename) for f in uploaded_files],
    }
    db.reference(f'uploads/{user_id}/{group_id}/metadata').set(metadata)

    # --- 6) Return Response ---
    return jsonify({
        'group_id': group_id,
        'files': results,
        'upload_time': metadata['upload_time'],
        'file_count': metadata['file_count'],
    }), 200

@file_extractor.route('/extractor/get-user-data', methods=['GET'])
def get_user_data():
    """Fetch all upload groups for the authenticated user."""
    # --- 1) Validate user/authentication ---
    firebase_user = session.get('user')  # Assume user info is stored in session
    if not firebase_user:
        return jsonify({'error': 'User not authenticated', 'uploads': []}), 200

    user_id = firebase_user.get('userId')
    if not user_id:
        return jsonify({'error': 'User ID not found'}), 400

    # --- 2) Fetch user's uploads from Firebase ---
    try:
        user_uploads_ref = db.reference(f'uploads/{user_id}')
        user_uploads = user_uploads_ref.get()  # Returns a dict of group_id -> {files, metadata}

        if not user_uploads:
            return jsonify({'message': 'No uploads found for the current user.', 'uploads': []}), 200

        # --- 3) Parse and build the response list ---
        uploads = []
        for group_id, group_data in user_uploads.items():
            metadata = group_data.get('metadata', {})
            # Extract files by excluding the 'metadata' key
            files_data = {
                k: v for k, v in group_data.items() if k != 'metadata'
            }

            uploads.append({
                'group_id': group_id,
                'upload_time': metadata.get('upload_time', 'Unknown'),
                'file_count': metadata.get('file_count', 0),
                'files': files_data,
            })

        # --- 4) Sort by upload_time descending (assuming ISO8601 format) ---
        sorted_uploads = sorted(
            uploads,
            key=lambda x: x['upload_time'],
            reverse=True
        )

        return jsonify({'uploads': sorted_uploads}), 200

    except Exception as e:
        print(f"Error fetching user data: {e}")
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
