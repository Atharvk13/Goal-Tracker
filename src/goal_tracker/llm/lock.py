"""One local model call at a time.

The machine has little memory, so scheduled agent runs and chat requests
share this lock instead of loading the model in parallel.
"""

import threading

MODEL_LOCK = threading.Lock()
