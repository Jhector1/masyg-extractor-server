import asyncio
from firebase_admin import firestore

from masyg_extractor.firebase.firebase_init import firebase_init

firebase_init()

# Create a Firestore client once (or create it as needed).
firestore_db = firestore.client()

async def get_firestore_client():
    # Offload firestore.client() to a new thread.
    return await asyncio.to_thread(firestore.client)

async def document_get(doc_ref):
    # Offload the blocking get() call.
    return await asyncio.to_thread(doc_ref.get)

async def document_set(doc_ref, data, merge=False):
    # Use a lambda to capture the merge flag.
    return await asyncio.to_thread(lambda: doc_ref.set(data, merge=merge))

async def document_update(doc_ref, data):
    return await asyncio.to_thread(lambda: doc_ref.update(data))

async def document_delete(doc_ref):
    return await asyncio.to_thread(doc_ref.delete)

async def stream_collection(coll_ref):
    # Return a list of documents from the collection.
    return await asyncio.to_thread(lambda: list(coll_ref.stream()))
