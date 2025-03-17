

from fastapi import APIRouter, Request, HTTPException, status

from firebase_admin import firestore
from masyg_extractor.services.file_extractor_service import *
from masyg_extractor.services.image_extractor_service import *
from firebase_admin import firestore
# from tool.extensions import sio

# Initialize Firestore client.
firestore_db = firestore.client()

# Create an API router for the extractor endpoints.
router = APIRouter(prefix="/extractor")

# # A thread pool for running blocking file processing code.
# executor = ThreadPoolExecutor(max_workers=5)
# LINE_ITEM_REGEX = re.compile(r'^(?P<base_path>.+)/line_items/(?P<index>\d+)$')
#
# def sanitize_generate_unique_filename(filename: str) -> str:
#     sanitized = re.sub(r'[./#$\[\]]', '_', filename)
#     unique_id = str(uuid.uuid4())
#     return f"{unique_id}_{sanitized}"
# # Example: an async version of process_file using asynchronous extraction functions.
# async def process_file_async(uploaded_file: UploadFile, user_id: str, group_id: str, client_id: str) -> Tuple[Any, str]:
#     try:
#         logger.info(f"Processing file: {uploaded_file.filename}")
#         asyncio.run_coroutine_threadsafe(send_log(f"✅ Processing file: {uploaded_file.filename}", user_room=client_id), MAIN_LOOP)
#         filename_lower = uploaded_file.filename.lower()
#         if filename_lower.endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
#             file_type = 'image'
#         elif filename_lower.endswith('.pdf'):
#             file_type = 'pdf'
#         else:
#             asyncio.create_task(
#                 send_log("❌ Unsupported file type", user_room=client_id))
#             return {'error': 'Unsupported file type'}, uploaded_file.filename
#
#         # Read file asynchronously and wrap in BytesIO.
#         file_bytes = await uploaded_file.read()
#         file_obj = io.BytesIO(file_bytes)
#
#         # Select extraction functions based on file type.
#         if file_type == 'pdf':
#             extractors = [
#                 extract_text_from_pdf,
#                 extract_text_from_pdf_image,
#                 extract_text_with_ocr_space,
#                 extract_text_from_scanned_pdf,
#                 extract_text_from_pdf_camelot,
#             ]
#         else:
#             extractors = [extract_text_from_image]
#
#         text = None
#         for extractor in extractors:
#             file_obj.seek(0)
#             logger.info(f"Trying extractor: {extractor.__name__}")
#             # Assume extractor is an async function.
#             text = await extractor(file_obj)
#             if text and text.strip():
#                 asyncio.create_task(send_log(f"✅ Text extraction succeeded with {extractor.__name__}", user_room=client_id))
#                 logger.info(f"Text extraction succeeded with {extractor.__name__}")
#                 break
#
#         if not text or not text.strip():
#             asyncio.create_task(
#                 send_log(f"⚠️ No text extracted from: {uploaded_file.filename}", user_room=client_id))
#             logger.warning(f"No text extracted from: {uploaded_file.filename}")
#             return {'error': 'Text extraction failed'}, uploaded_file.filename
#
#         extractor_index = 0
#         parsed_content = None
#         while extractor_index < len(extractors):
#             try:
#                 # Remove sensitive data asynchronously if possible.
#                 text, sensitive = await asyncio.to_thread(remove_sensitive_data, text)
#                 logger.info(f"Sensitive data removed: {sensitive}")
#
#                 # Process text with GPT asynchronously (wrap synchronous call if needed).
#                 json_content = await process_text_with_gpt(text)
#                 parsed_content = json.loads(json_content)
#                 logger.info(f"Parsed JSON content: {parsed_content}")
#
#                 if not isinstance(parsed_content.get('line_items'), list):
#                     raise ValueError("Invalid JSON format: 'line_items' is not a list")
#                 if 'vendor_name' not in parsed_content or 'date' not in parsed_content:
#                     raise ValueError("Invalid JSON format: Missing 'vendor_name' or 'date'")
#                 break
#             except (json.JSONDecodeError, ValueError) as e:
#                 logger.warning(f"Error parsing JSON with extractor {extractors[extractor_index].__name__}: {e}")
#                 extractor_index += 1
#                 if extractor_index < len(extractors):
#                     file_obj.seek(0)
#                     text = await extractors[extractor_index](file_obj)
#                 else:
#                     asyncio.create_task(
#                         send_log("⚠️ No more extractors to retry", user_room=client_id))
#                     logger.warning("No more extractors to retry.")
#                     return {'error': 'Text processing failed with all extractors'}, uploaded_file.filename
#
#         logger.info(f"Final successful extractor index: {extractor_index}")
#         sanitized_filename = sanitize_generate_unique_filename(uploaded_file.filename)
#         file_doc_ref = (
#             firestore_db.collection("users")
#             .document(user_id)
#             .collection("groups")
#             .document(group_id)
#             .collection("files")
#             .document(sanitized_filename)
#         )
#         file_doc_ref.set(parsed_content)
#         return parsed_content, sanitized_filename
#
#     except Exception as e:
#         asyncio.create_task(
#             asyncio.create_task(
#                 send_log(f"❌ Error processing file: {uploaded_file.filename}", user_room=client_id)))
#         logger.exception(f"Error processing file: {uploaded_file.filename}")
#         return {'error': str(e)}, uploaded_file.filename
#
# # If you convert process_file to async, you can define:
# async def process_file_wrapper(idx: int, file: UploadFile, user_id: str, group_id: str, client_id: str) -> Tuple[int, str, Any]:
#     # Here, we assume process_file is an async function.
#     # Alternatively, if process_file remains synchronous but you have async extraction functions,
#     # make sure to 'await' every async operation.
#     result = await process_file_async(file, user_id, group_id, client_id)
#     return (idx, result[1], result[0])
#
# # ---------------------------------------------------
# # Asynchronous processing for multiple files.
# # ---------------------------------------------------
# async def process_files_in_parallel(files, user_id, group_id, client_id):
#     tasks = [
#         process_file_wrapper(idx, f, user_id, group_id, client_id)
#         for idx, f in enumerate(files)
#     ]
#     results_list = await asyncio.gather(*tasks, return_exceptions=True)
#     results = {}
#     for idx, res in enumerate(results_list):
#         if isinstance(res, Exception):
#             logger.exception(f"Error processing file at index {idx}: {res}")
#             results[idx] = {'error': str(res)}
#         else:
#             index, sanitized_filename, parsed_content = res
#             results[index] = {
#                 'sanitized_filename': sanitized_filename,
#                 'parsed_content': parsed_content
#             }
#     return results
#
# def generate_group_id():
#     return datetime.now().strftime('%Y%m%d%H%M%S')
# # Dependency to retrieve firebase user information from session.

async def get_firebase_user(request: Request):
    firebase_user = request.session.get("user")
    if not firebase_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not authenticated"
        )
    # Since we're storing a dict, return it directly.
    if isinstance(firebase_user, dict):
        return firebase_user
    try:
        return json.loads(firebase_user)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid firebase user data"
        )
