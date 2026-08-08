# Vertical Slice Workflow

```text
Explore
→ First Runnable Slice
→ Owner Validation
→ Harden by Observed Risk
→ Promote to Maintained Code
→ Ship
```

Explore tìm đường đi ngắn nhất qua input, core behavior và output. First Runnable Slice dùng fixture nhỏ và cho kết quả quan sát được. Owner Validation xác nhận product direction trước khi harden. Harden chỉ theo risk đã quan sát. Promotion xóa prototype/dead code và đưa logic cần giữ vào maintained boundary. Ship chỉ khi gate phù hợp risk tier PASS.
