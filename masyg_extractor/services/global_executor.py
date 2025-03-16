import concurrent.futures

DEFAULT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=5)
import asyncio

MAIN_LOOP = None  # This will be set in the main entry point.
