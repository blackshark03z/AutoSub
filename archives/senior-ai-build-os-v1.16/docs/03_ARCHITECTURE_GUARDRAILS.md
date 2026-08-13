# Architecture Guardrails

Mặc định modular monolith, data ownership rõ, dependency hướng vào core behavior và ít deployable/process. Complexity budget phải được ghi ở Project Contract. Abstraction cần variation thật hoặc tested external boundary.

Refactor khi coupling gây bug, thay đổi lặp qua nhiều nơi, test seam cần thiết hoặc scale/runtime evidence chứng minh. Sau 3–5 shipping tasks có thể dành tối đa khoảng một task cleanup; không continuous polishing. Mọi architecture change ngoài budget cần owner approval.
