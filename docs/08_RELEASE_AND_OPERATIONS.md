# Release and Operations

Development loop chạy từ source với fixture nhỏ. Không package/install/audit sau mọi edit. Preflight kiểm tra disk, RAM/GPU, process/port, artifact lineage, data protection và provider cost.

Chạy full suite một lần tại release gate phù hợp, package một lần sau accepted workflow. Long-running process phải có PID, port, log, timeout, cleanup và owner. Release kiểm tra install/start/stop; activation chỉ sau evidence và authorization.
