import json

from flask import Blueprint, request, jsonify, session, make_response
import PyPDF2
from concurrent.futures import ThreadPoolExecutor
import logging
from firebase_admin import db
import uuid
from datetime import datetime
from itertools import chain
import re
from services.file_extractor_service import *
from services.image_extractor_service import *
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

def process_file(uploaded_file, user_id, group_id):
    try:
        logging.info(f"Processing file: {uploaded_file.filename}")
        # Determine file type (PDF or Image)
        if uploaded_file.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff',)):
            file_type = 'image'
        elif uploaded_file.filename.lower().endswith('.pdf'):
            file_type = 'pdf'
        else:
            return {'error': 'Unsupported file type'}, uploaded_file.filename

        pdf_text_extractors = [
            extract_text_from_pdf,
            extract_text_from_pdf_image,
            extract_text_with_ocr_space,
            extract_text_from_scanned_pdf,
            extract_text_from_pdf_camelot,

        ]
        image_text_extractors = [
            extract_text_from_image,
        ]
        text_extractors = pdf_text_extractors if file_type == 'pdf' else image_text_extractors

        # Attempt extraction with each extractor
        text = None
        for index, extractor in enumerate(text_extractors):
            uploaded_file.seek(0)  # Reset file pointer for each attempt
            logging.info(f"Trying extractor: {extractor.__name__}")
            text = extractor(uploaded_file)
            if text and text.strip():
                logging.info(f"Text extraction succeeded with {extractor.__name__}")
                break

        # Handle total failure of text extraction
        if not text or not text.strip():
            logging.warning(f"No text extracted from: {uploaded_file.filename}")
            return {'error': 'Text extraction failed'}, uploaded_file.filename

        # Process extracted text with GPT, retrying with remaining extractors if needed
        while True:
            try:
                json_content = process_text_with_gpt(text)
                parsed_content = json.loads(json_content)
                if isinstance(parsed_content, list):
                    break  # Exit loop if valid JSON content is obtained
            except (json.JSONDecodeError, ValueError) as e:
                logging.warning(f"Error parsing JSON content: {e}")

            index += 1
            if index >= len(text_extractors):
                logging.warning("No more extractors to retry.")
                return {'error': 'Text processing failed with all extractors'}, uploaded_file.filename

            uploaded_file.seek(0)  # Reset file pointer
            next_extractor = text_extractors[index]
            logging.info(f"Retrying with extractor: {next_extractor.__name__}")
            text = next_extractor(uploaded_file)
            # print(text)
            # print(json_content)

        # Log the successful extractor index
        logging.info(f"Final successful extractor index: {index}")

        # Parse JSON content and store records in Firebase
        records = parse_json_to_dataframe(json_content).to_dict(orient='records')
        sanitized_filename = sanitize_filename(uploaded_file.filename)
        db.reference(f'uploads/{user_id}/{group_id}/{sanitized_filename}').set(records)
        return records, uploaded_file.filename

    except Exception as e:
        logging.exception(f"Error processing file: {uploaded_file.filename}")
        return {'error': str(e)}, uploaded_file.filename

@file_extractor.route('/extractor/extract-data', methods=['POST'])
def extract_data():
    """Extract data from multiple PDF files using GPT, store in Firebase, and return metadata."""
    def validate_request():
        """Validate the incoming request for files and user authentication."""
        if 'files' not in request.files:
            return {'error': 'No files uploaded'}, 400
        uploaded_files = request.files.getlist('files')
        if not uploaded_files:
            return {'error': 'No files provided'}, 400
        firebase_user = session.get('user')
        if not firebase_user:
            return {'error': 'User not authenticated'}, 401
        user_id = firebase_user.get('userId')
        if not user_id:
            return {'error': 'User ID not found'}, 400
        return None, uploaded_files, user_id

    def generate_group_id():
        """Generate a unique group ID based on the current timestamp."""
        return datetime.now().strftime('%Y%m%d%H%M%S')

    def process_files_in_parallel(files, process_func):
        """Process files using the given function in parallel."""
        results = {}
        futures = [executor.submit(process_func, f) for f in files]
        for future in futures:
            try:
                data, filename = future.result()
                results[filename] = data
            except Exception as e:
                logging.exception(f"Error processing file: {e}")
                results[filename] = {'error': str(e)}
        return results

    # Step 1: Validate Request
    error_response, uploaded_files, user_id = validate_request()
    if error_response:
        return jsonify(error_response), error_response[1]

    # Step 2: Generate Unique Group ID
    group_id = generate_group_id()

    # Step 3: Process Files in Parallel
    def process_file_wrapper(pdf_file):
        return process_file(pdf_file, user_id, group_id)

    results = process_files_in_parallel(uploaded_files, process_file_wrapper)
    flat_list = list(chain.from_iterable(r for r in results.values() if isinstance(r, list)))

    # Step 4: Handle Empty Results
    if not flat_list:
        return jsonify({'error': 'No valid data extracted from the uploaded files'}), 400

    # Step 5: Store Metadata
    metadata = {
        'upload_time': datetime.now().isoformat(),
        'file_count': len(uploaded_files),
        'files': [sanitize_filename(f.filename) for f in uploaded_files],
    }
    db.reference(f'uploads/{user_id}/{group_id}/metadata').set(metadata)

    # Step 6: Return Response
    return jsonify({
        'group_id': group_id,
        'files': results,
        'upload_time': metadata['upload_time'],
        'file_count': metadata['file_count'],
    }), 201


# @file_extractor.route('/extractor/extract-data', methods=['POST'])
# def extract_data():
#     """Extract data from multiple PDF files using GPT, store in Firebase, and return metadata."""
#     # --- 1) Validate Request ---
#     if 'files' not in request.files:
#         return jsonify({'error': 'No files uploaded'}), 400
#     uploaded_files = request.files.getlist('files')
#     if not uploaded_files:
#         return jsonify({'error': 'No files provided'}), 400
#
#     # --- 2) Validate User ---
#     firebase_user = session.get('user')  # Suppose user info is stored in session
#     if not firebase_user:
#         return jsonify({'error': 'User not authenticated'}), 401
#     user_id = firebase_user.get('userId')
#     if not user_id:
#         return jsonify({'error': 'User ID not found'}), 400
#
#     # --- 3) Generate Unique Group ID ---
#     group_id = datetime.now().strftime('%Y%m%d%H%M%S')  # e.g., "20250103123045"
#
#     results = {}  # Will hold file_name -> extracted data
#
#     def process_file(pdf_file):
#         try:
#             logging.info(f"Attempting text extraction for {pdf_file.filename} using PyMuPDF...")
#
#             text_extractors = [
#                 extract_text_from_pdf,
#                 extract_text_from_pdf_image,
#                 extract_text_with_ocr_space,
#                 extract_text_from_scanned_pdf,
#                 extract_text_from_pdf_camelot
#             ]
#
#             # Attempt text extraction using available extractors
#             text = None
#             for extractor in text_extractors:
#                 logging.info(f"Attempting extraction with {extractor.__name__}...")
#                 text = extractor(pdf_file)
#                 # print(text)
#                 if text and text.strip():
#                     logging.info(f"Text extraction succeeded with {extractor.__name__}.")
#                     break
#
#             # Handle total failure of text extraction
#             if not text or not text.strip():
#                 logging.error(f"Text extraction failed entirely for {pdf_file.filename}.")
#                 return {'error': 'Text extraction failed'}, pdf_file.filename
#
#             # Process extracted text with GPT until successful or all extractors are tried
#             json_content = process_text_with_gpt(text)
#             for extractor in text_extractors[text_extractors.index(extractor):]:
#
#                 while not json_content:
#                     print(json_content)
#                     text = extractor(pdf_file)
#                     json_content = process_text_with_gpt(text)
#
#             # Handle failure to process text with GPT
#             if not json_content:
#                 logging.error(f"Processing text with GPT failed for {pdf_file.filename}.")
#                 return [], pdf_file.filename
#
#             # Parse JSON and save records
#             records = parse_json_to_dataframe(json_content).to_dict(orient='records')
#             sanitized_filename = sanitize_filename(pdf_file.filename)
#             db.reference(f'uploads/{user_id}/{group_id}/{sanitized_filename}').set(records)
#
#             return records, pdf_file.filename
#
#         except Exception as e:
#             logging.exception(f"An error occurred while processing {pdf_file.filename}.")
#             return {'error': str(e)}, pdf_file.filename
#
#     # --- 4) Parallel File Processing ---
#     futures = [executor.submit(process_file, f) for f in uploaded_files]
#     for future in futures:
#         data, filename = future.result()
#         results[filename] = data
#     flat_list = list(chain.from_iterable(results.values()))
#     # print(len(flat_list))
#     # print(results)
#
#     if len(flat_list) <= 0:
#         return jsonify({'error': 'This file does not contain the requested keywords'}), 400
#
#     # --- 5) Store Group Metadata ---
#     metadata = {
#         'upload_time': datetime.now().isoformat(),
#         'file_count': len(uploaded_files),
#         'files': [sanitize_filename(f.filename) for f in uploaded_files],
#     }
#     db.reference(f'uploads/{user_id}/{group_id}/metadata').set(metadata)
#
#     # --- 6) Return Response ---
#     return jsonify({
#         'group_id': group_id,
#         'files': results,
#         'upload_time': metadata['upload_time'],
#         'file_count': metadata['file_count'],
#     }), 201
@file_extractor.route('/extractor/get-user-data', methods=['GET'])
def get_user_data():
    """Fetch all upload groups for the authenticated user."""
    # Validate user authentication
    firebase_user = session.get('user')
    if not firebase_user:
        return jsonify({'error': 'User not authenticated', 'uploads': []}), 200

    user_id = firebase_user.get('userId')
    if not user_id:
        return jsonify({'error': 'User ID not found'}), 400

    try:
        # Fetch user's uploads
        user_uploads_ref = db.reference(f'uploads/{user_id}')
        user_uploads = user_uploads_ref.get()

        if not user_uploads:
            return jsonify({'message': 'No uploads found for the current user.', 'uploads': []}), 200

        # Build response
        uploads = []
        for group_id, group_data in user_uploads.items():
            metadata = group_data.get('metadata', {})
            files_data = {k: v for k, v in group_data.items() if k != 'metadata'}

            uploads.append({
                'group_id': group_id,
                'upload_time': metadata.get('upload_time', '1970-01-01T00:00:00Z'),
                'file_count': metadata.get('file_count', 0),
                'files': files_data,
            })

        sorted_uploads = sorted(
            uploads,
            key=lambda x: x['upload_time'],
            reverse=True
        )

        return jsonify({'uploads': sorted_uploads}), 200

    except Exception as e:
        logging.error(f"Failed to fetch user data: {e}")
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


@file_extractor.route('/extractor/delete/groups/<group_id>/files/<file_name>/records/<record_key>',
                      methods=['DELETE'])
def delete_record(group_id, file_name, record_key):
    """
    Delete a specific record (e.g., 0, 1, etc.) from Firebase. If the group has only one record left, delete the entire group.
    """

    if not group_id or not file_name or not record_key:
        return jsonify({'error': 'Missing required parameters: group_id, file_name, record_key'}), 400

    # --- 2) Validate User Authentication ---
    firebase_user = session.get('user')  # Assuming user info is stored in session
    if not firebase_user:
        return jsonify({'error': 'User not authenticated'}), 401
    user_id = firebase_user.get('userId')
    if not user_id:
        return jsonify({'error': 'User ID not found'}), 403

    # --- 3) Build Firebase Reference Path ---
    sanitized_file_name = sanitize_filename(file_name)
    record_path = f'uploads/{user_id}/{group_id}/{sanitized_file_name}/{record_key}'
    group_path = f'uploads/{user_id}/{group_id}'

    # --- 4) Check If Record Exists ---
    record_ref = db.reference(record_path)

    if not record_ref.get():
        return jsonify({'error': 'Record not found'}), 404

    # --- 5) Delete Record ---
    record_ref.delete()

    # --- 6) Check If Group Becomes Empty ---
    group_ref = db.reference(group_path)
    group_data = group_ref.get()

    if group_data:
        remaining_files = {k: v for k, v in group_data.items() if k != 'metadata'}

        # If each remaining_files[k] is a list:
        remaining_records = list(chain.from_iterable(remaining_files.values()))

        if len(remaining_records) == 0:
            group_ref.delete()
            return jsonify(
                {
                    'message': f'Record {record_key} deleted. '
                               f'Group {group_id} also deleted as it became empty.'
                }
            ), 200

    return jsonify({'message': f'Record {record_key} successfully deleted from {file_name}'}), 200

@file_extractor.route('/extractor/update/groups/<group_id>/files/<file_name>/records/<record_key>',
                      methods=['PUT'])
def update_record( group_id, file_name, record_key):
    """
    Update a specific record in Firebase.
    """
    # --- 1) Validate User Authentication ---
    firebase_user = session.get('user')  # Assuming user info is stored in session
    if not firebase_user:
        return jsonify({'error': 'User not authenticated'}), 401
    user_id = firebase_user.get('userId')
    if not user_id:
        return jsonify({'error': 'User ID not found'}), 403

    # --- 2) Validate Request Data ---
    updated_data = request.json

    if not request.json:
        return jsonify({'error': 'Missing request body'}), 400

    # Extract the updated data from the request body
    updated_data = request.json

    # --- 3) Build Firebase Reference Path ---
    sanitized_file_name = file_name  # Assuming `file_name` is safe or already sanitized
    record_path = f'uploads/{user_id}/{group_id}/{sanitized_file_name}/{record_key}'

    # --- 4) Check If Record Exists ---
    record_ref = db.reference(record_path)
    if not record_ref.get():
        return jsonify({'error': 'Record not found'}), 404

    # --- 5) Update Record ---
    record_ref.update(updated_data)

    # --- 6) Return Success Response ---
    return jsonify({'message': f'Record {record_key} successfully updated.'}), 200
#
# def process_file(pdf_file):
#     try:
#         logging.info(f"Attempting text extraction for {pdf_file.filename} using PyMuPDF...")
#         tool_index = 0
#         text_extractors = [
#             extract_text_from_pdf,
#             extract_text_from_pdf_image,
#             extract_text_with_ocr_space,
#             extract_text_from_scanned_pdf,
#             extract_text_from_pdf_camelot
#         ]
#         text=""
#         for extractor in text_extractors:
#             logging.info(f"Attempting extraction with {extractor.__name__}...")
#             text = extractor(pdf_file)
#             print(text)
#             if text and text.strip():
#                 logging.info(f"Text extraction succeeded with {extractor.__name__}.")
#                 break
#             tool_index += 1
#
#         # 3. Handle total failure of text extraction
#         if not text or text.strip() == "":
#             logging.info(f"Text extraction failed entirely for {pdf_file.filename}.")
#             return {'error': 'Text extraction failed'}, pdf_file.filename
#
#         json_content = process_text_with_gpt(text)
#         while not json_content and tool_index < len(text_extractors):
#             text= text_extractors[tool_index](text)
#             json_content = process_text_with_gpt(text)
#
#         if not json_content:
#             return [], pdf_file.filename
#         records = parse_json_to_dataframe(json_content).to_dict(orient='records')
#         sanitized = sanitize_filename(pdf_file.filename)
#         # db.reference(f'uploads/{user_id}/{group_id}/{sanitized}').set(records)
#
#         return records, pdf_file.filename
#     except Exception as e:
#         return {'error': str(e)}, pdf_file.filename