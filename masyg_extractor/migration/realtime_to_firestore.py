# import json
# from datetime import datetime
# import firebase_admin
# from firebase_admin import firestore
#
# from firebase.firebase_init import firebase_init
#
# # Initialize the Firebase Admin SDK (your custom firebase_init must call firebase_admin.initialize_app)
# firebase_init()
#
# # Get the Firestore client.
# db = firestore.client()
#
# # Define the batch write limit.
# MAX_BATCH_SIZE = 500
#
# def commit_batch(batch, counter):
#     """Commits the current batch if there are pending writes, then returns a fresh batch."""
#     if counter > 0:
#         batch.commit()
#         print(f"Committed batch of {counter} writes.")
#     return db.batch(), 0
#
# # Initialize batch and counter.
# batch = db.batch()
# batch_counter = 0
#
# # Dummy fallback values.
# DUMMY_BLOB = "dummy_blob"
# DUMMY_UPLOAD_TIME = "2025-03-04T01:49:04.764148"
#
# DUMMY_VENDOR_NAME = "N/A"
# DUMMY_DATE = "N/A"
# DUMMY_TAX = "N/A"
#
# DUMMY_ITEM_NAME = "N/A"
# DUMMY_CATEGORY = "N/A"
# DUMMY_DESCRIPTION = "N/A"
# DUMMY_QUANTITY = "N/A"
# DUMMY_UNIT_PRICE = "N/A"
#
# # Path to your exported Realtime Database JSON.
# JSON_PATH = "/Users/admin/downloads/masyg-extractor-db-default-rtdb-export (1).json"
#
# with open(JSON_PATH, "r") as f:
#     data = json.load(f)
#
# # Extract top-level nodes from your export.
# users = data.get("users", {})
# uploads = data.get("uploads", {})
#
# for user_id, user_info in users.items():
#     try:
#         # --- (1) Create the User Document ---
#         user_doc = {
#             "email": user_info.get("email", "dummy@example.com"),
#             "hasUsedTrial": user_info.get("hasUsedTrial", False),
#             "isSubscribed": user_info.get("isSubscribed", False),
#             "password": user_info.get("password", "dummy_password"),
#             "username": user_info.get("username", "dummy_username"),
#             "createdAt": datetime.now(),
#             "updatedAt": datetime.now()
#         }
#         user_ref = db.collection("users").document(user_id)
#         batch.set(user_ref, user_doc)
#         batch_counter += 1
#         print(f"User {user_id} scheduled for migration.")
#
#         # --- (2) Process Groups (uploads) for the User ---
#         user_uploads = uploads.get(user_id, {})
#         for group_id, group_data in user_uploads.items():
#             # (A) Transform the group's metadata.
#             original_metadata = group_data.get("metadata", {})
#             filenames = original_metadata.get("files", [])  # Originally an array of strings.
#             file_count = original_metadata.get("file_count", len(filenames))
#             upload_time = original_metadata.get("upload_time", DUMMY_UPLOAD_TIME)
#
#             # Build new metadata "files" array: each filename becomes an object.
#             new_files_array = []
#             for filename in filenames:
#                 new_files_array.append({
#                     "content": DUMMY_BLOB,
#                     "filename": filename
#                 })
#             updated_metadata = {
#                 "file_count": file_count,
#                 "files": new_files_array,
#                 "upload_time": upload_time
#             }
#
#             # Create the group document with the transformed metadata.
#             group_ref = user_ref.collection("groups").document(group_id)
#             group_doc = {
#                 "metadata": updated_metadata
#             }
#             batch.set(group_ref, group_doc)
#             batch_counter += 1
#             print(f"  Group {group_id} scheduled for migration for user {user_id}.")
#
#             # (B) Process Files for the Group.
#             # Iterate over keys in group_data other than "metadata".
#             for file_key, file_details in group_data.items():
#                 if file_key == "metadata":
#                     continue
#                 # Assume file_details is an array of objects (each representing a line item).
#                 if not isinstance(file_details, list):
#                     continue  # Skip if not list.
#                 line_items = []
#                 for item in file_details:
#                     line_item = {
#                         "item_name": DUMMY_ITEM_NAME,
#                         "category": DUMMY_CATEGORY,
#                         "description": item.get("Description", DUMMY_DESCRIPTION),
#                         "quantity": item.get("Qty", DUMMY_QUANTITY),
#                         "unit_price": item.get("Ext_Price", item.get("Price", DUMMY_UNIT_PRICE))
#                     }
#                     line_items.append(line_item)
#                 file_doc = {
#                     "vendor_name": DUMMY_VENDOR_NAME,
#                     "date": DUMMY_DATE,
#                     "tax": DUMMY_TAX,
#                     "line_items": line_items
#                 }
#                 # Create a file document in the group's "files" subcollection using file_key as the document ID.
#                 file_ref = group_ref.collection("files").document(file_key)
#                 batch.set(file_ref, file_doc)
#                 batch_counter += 1
#                 print(f"    File '{file_key}' scheduled for migration in group {group_id}.")
#
#                 if batch_counter >= MAX_BATCH_SIZE:
#                     batch, batch_counter = commit_batch(batch, batch_counter)
#
#             if batch_counter >= MAX_BATCH_SIZE:
#                 batch, batch_counter = commit_batch(batch, batch_counter)
#
#         # --- (3) Create a Dummy QuickBooks Integration Document ---
#         qb_dummy = {
#             "transactionType": "dummy",
#             "docNumber": "dummy",
#             "customerId": "dummy",
#             "date": datetime.now(),
#             "amount": 0,
#             "metadata": {
#                 "syncToken": "dummy",
#                 "otherField": "dummy"
#             }
#         }
#         qb_ref = user_ref.collection("integrations_legacy").document("QuickBooks")
#         dummy_doc_ref = qb_ref.collection("dummy").document("dummyTransaction")
#         batch.set(dummy_doc_ref, qb_dummy)
#         batch_counter += 1
#         print(f"  Dummy QuickBooks integrations scheduled for user {user_id}.")
#
#         if batch_counter >= MAX_BATCH_SIZE:
#             batch, batch_counter = commit_batch(batch, batch_counter)
#
#     except Exception as e:
#         print(f"Error migrating user {user_id}: {e}")
#
# # Commit any remaining operations.
# batch, batch_counter = commit_batch(batch, batch_counter)
# print("Migration complete.")
