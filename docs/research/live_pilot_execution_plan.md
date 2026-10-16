# AutoSub Cloud V2 — Phase C: Live Pilot Execution Plan

**Document Version:** 1.0.0  
**Date:** 2026-10-16  
**Status:** PROPOSED — STRICTLY BLOCKED UNTIL EXPLICIT OWNER APPROVAL  
**Work Package:** WP-PILOT-001-FINAL  
**Project:** AutoSub Cloud V2  
**Branch:** `feat/pilot-e2e-local-sub-overlay`  
**Worker:** Gemini  
**Tech Lead / Auditor:** ChatGPT  
**Priority:** P0  
**Workflow Standard:** Native Workflow Kit v1.3  

---

## 1. Cảnh Báo An Toàn & Ranh Giới Bắt Buộc

> [!CAUTION]
> **KẾ HOẠCH NÀY CHỈ LÀ PHƯƠNG ÁN ĐỀ XUẤT KỸ THUẬT VÀ ĐANG BỊ KHÓA HOÀN TOÀN (BLOCKED).**  
> Tuyệt đối KHÔNG tự ý thực hiện bất kỳ hành động nào dưới đây cho đến khi có **văn bản chấp thuận rõ ràng của Owner**:  
> - **KHÔNG** tải media payload thật từ Bilibili hoặc Kylin về máy.  
> - **KHÔNG** cài đặt/cập nhật phần mềm hay sửa đổi cấu hình máy host của Operator.  
> - **KHÔNG** kích hoạt Docker, WSL hoặc các tác vụ GPU workload nặng.  
> - **KHÔNG** sử dụng tài khoản, cookie, VIP credential cá nhân của Owner.  
> - **KHÔNG** khởi chạy Cloud Download Worker hoặc thuê hạ tầng GPU đám mây.  
> - **KHÔNG** gọi API Gemini trả phí vượt ngoài ngân sách được cấp phép riêng.  
> - **KHÔNG** upload media lên bất kỳ dịch vụ lưu trữ bên thứ ba nào.  
> - **KHÔNG** tìm cách bẻ khóa giao thức app, vượt DRM hay rào cản truy cập.  
> - **KHÔNG** merge nhánh `feat/pilot-e2e-local-sub-overlay` vào `main`.

---

## 2. Chi Tiết Kế Hoạch Đề Xuất (8 Hạng Mục Bắt Buộc)

### 2.1 Nguồn Được Đề Xuất Thử Nghiệm (Proposed Source)
- **Nguồn được chọn:** **Source A — Bilibili (`https://b23.tv/gK7gvDO`)**, Part 1 (`p=1`, CID `41959818910`).
- **Lý do lựa chọn:**
  - Nguồn A có luồng stream công khai ở độ phân giải 720p/480p không đòi hỏi tài khoản VIP hay cookie cá nhân.
  - Không có tín hiệu mã hóa DRM/CENC.
  - Phù hợp nhất để kiểm chứng chuỗi liên kết: Link → Canonical BV → Part 1 Selection → Bounded Media Excerpt → OCR → Dịch → Mask → Overlay tiếng Việt.
- **Nguồn Source B (Kylin):** **KHÔNG ĐỀ XUẤT** tải live vì bị rào cản `APP_ONLY_RESTRICTED`. Việc cố gắng vượt rào cản app vi phạm chính sách tuân thủ kỹ thuật.

### 2.2 Độ Dài Đoạn Thử Nghiệm (Sample Duration)
- **Độ dài đề xuất:** Đúng **60 giây** (1 phút trích xuất từ đầu Part 1: từ `00:00:00` đến `00:01:00`).
- **Lý do giới hạn 60s:**
  - Đủ thời lượng để chứa khoảng 8–12 câu thoại tiếng Trung tiêu biểu của thể loại AI Manju.
  - Dung lượng tải về ước tính chỉ khoảng **8–12 MB** ở profile 720p 15fps, không gây áp lực băng thông.
  - Tuyệt đối không tải toàn bộ 7.8 giờ của Part 1 hay 11.9 giờ của cả container.

### 2.3 Các Thao Tác & Engine Sẽ Dùng (Operations & Engines)
1. **Ingestion:** Tải một đoạn stream phân đoạn (fragment/range request) dài 60s thông qua yt-dlp hoặc HTTP range header với URL stream công khai đã phân giải.
2. **Video Probe:** Dùng FFprobe cục bộ đo container, xác thực video 720p và audio AAC.
3. **OCR Phụ đề gốc:** Dùng PaddleOCR cục bộ (tiến trình nhẹ) quét các khung hình chứa chữ trong ROI đáy để trích xuất chữ Hán và tọa độ bbox thật.
4. **Dịch Thuật Tiếng Việt:** 
   - *Phương án ưu tiên:* Dùng bộ từ điển/mô hình dịch offline Argos zh→vi nếu có sẵn.
   - *Phương án dự phòng:* Dùng Gemini API với key đã cấu hình an toàn trong `secrets\gemini_api.txt` cho đúng 8–12 câu thoại (ước tính < 500 tokens, chi phí < 0.001 USD).
5. **Render `LOCAL_SUB_MASK_OVERLAY`:** 
   - FFmpeg `drawbox` màu đen đặc `@1.0` che kín đúng bbox chữ Trung gốc (padding 2–4px).
   - FFmpeg `subtitles` (libass) đè phụ đề tiếng Việt cỡ tương đương, căn giữa chuẩn xác.
6. **Output Validation:** FFprobe kiểm tra độ toàn vẹn, đảm bảo drift = 0.00ms và audio không bị mất.

### 2.4 Môi Trường Dự Kiến (Target Environment)
- **Máy trạm:** Môi trường Windows 11/10 x86_64 hiện tại của Operator.
- **Python:** Python 3.11.9.
- **FFmpeg:** FFmpeg 9.0.1-essentials đã cài sẵn trên hệ thống.
- **Bộ nhớ đĩa khả dụng yêu cầu:** Tối thiểu 2 GB trống tại ổ đĩa phát triển.

### 2.5 Tài Nguyên Dự Kiến (Resource Consumption)
- **Băng thông mạng tải về:** ~10 MB.
- **Bộ nhớ RAM đỉnh:** < 1.5 GB RAM trong lúc chạy OCR cục bộ trên đoạn 60s.
- **Thời gian xử lý dự kiến:** ~45–75 giây.
- **Chi phí đám mây:** 0 VND (nếu dịch offline) hoặc < 50 VND (nếu dùng Gemini Flash 1.5 cho 12 câu).

### 2.6 Tác Động Lên Máy Host (Host Impact)
- **Không thay đổi cấu hình hệ thống:** Không sửa registry, không đổi biến môi trường toàn cục, không cài service ngầm.
- **Toàn bộ tệp trung gian được cô lập:** Nằm trọn vẹn trong thư mục tạm `scratch/live_pilot_run/`.
- **Tệp nguồn gốc:** Được lưu dưới dạng read-only và xóa ngay sau khi nghiệm thu.

### 2.7 Tiêu Chí PASS / FAIL (Acceptance Criteria)
Một bài chạy Live Pilot chỉ được xem là **PASS** khi thỏa mãn đồng thời cả 7 tiêu chí:
1. Tải về thành công đoạn 60s mà không phát sinh lỗi HTTP 403 / 429.
2. FFprobe xác thực tệp đầu ra đọc được, đúng 60s, sai lệch drift < 50ms.
3. Không làm biến dạng hay mất luồng âm thanh gốc.
4. Chữ Hán gốc được che kín hoàn toàn bởi hộp đen đặc nhỏ gọn, không để lộ viền chữ.
5. Phụ đề tiếng Việt hiển thị rõ ràng, đúng ngữ nghĩa, cỡ chữ tương đương (sai số <= 15%), nằm đúng vị trí chữ cũ.
6. Hộp đen biến mất hoàn toàn trong các khoảng lặng giữa các câu thoại.
7. Toàn bộ tiến trình kết thúc với exit code = 0.

### 2.8 Quy Trình Dừng Khẩn Cấp, Rollback & Dọn Dẹp (Stop, Rollback & Cleanup)
- **Dừng khẩn cấp:** Nếu phát hiện lỗi mạng kéo dài > 30s, dung lượng tệp vượt quá 50 MB, hoặc CPU/RAM tăng bất thường > 80%: Tiến trình lập tức gửi tín hiệu `SIGTERM` hủy FFmpeg và dừng script.
- **Rollback:** Không ghi đè bất kỳ tệp mã nguồn hoặc cấu hình nào của dự án.
- **Dọn dẹp:** Xóa toàn bộ thư mục tạm `scratch/live_pilot_run/` và các tệp media tải về bằng lệnh dọn dẹp một chạm `python tools/cleanup_pilot_artifacts.py`.

---

## 3. Khuyến Nghị Gửi Tech Lead & Owner

Gemini đề xuất:
1. **Chốt nghiệm thu các Gate G1 → G5 (Offline scope)** của Work Package WP-PILOT-001-FINAL.
2. Giữ nguyên trạng thái **`G6 (Live E2E) = NOT RUN`**.
3. Chờ Owner xem xét kế hoạch trên và đưa ra quyết định có cấp phép chạy đoạn mẫu 60 giây hay không.

---
*Kế hoạch thực thi Live Pilot lập theo tiêu chuẩn Native Workflow Kit v1.3.*
