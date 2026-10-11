"""Client gọi Google Gemini API: (1) phân loại sentiment chính xác hơn lexicon,
(2) sinh khuyến nghị đầu tư chi tiết bằng tiếng Việt.

Mọi hàm ở đây RAISE GeminiUnavailable thay vì tự fallback — nơi gọi
(sentiment.py, screener.py) chịu trách nhiệm try/except. Nhờ vậy thiếu API
key / lỗi / hết quota KHÔNG làm crash web."""
from __future__ import annotations
import json
import concurrent.futures

from app.config import GEMINI_API_KEY, USE_GEMINI, GEMINI_MODEL
from app.utils.cache import gemini_cache, cached


class GeminiUnavailable(Exception):
    """USE_GEMINI=false, thiếu key, hết quota, hoặc lỗi gọi API."""


def _get_model():
    if not USE_GEMINI:
        raise GeminiUnavailable("USE_GEMINI=false — Gemini đang tắt.")
    if not GEMINI_API_KEY:
        raise GeminiUnavailable("Thiếu GEMINI_API_KEY trong .env.")
    try:
        from google import genai
    except ImportError:
        raise GeminiUnavailable("Chưa cài thư viện: pip install google-genai")
    client = genai.Client(api_key=GEMINI_API_KEY)
    return client


def _friendly(err: str) -> str:
    e = err.lower()
    if "429" in e or "resource_exhausted" in e or "quota" in e:
        return ("Gemini đã hết quota (gói free chỉ ~20 lượt/ngày/model). Thử lại sau hoặc đổi GEMINI_MODEL / API key khác "
                "(gói Gemini Pro của Google One KHÔNG tính cho API — cần bật billing trong Google AI Studio).")
    if "404" in e or "not_found" in e or "not found" in e:
        return f"Model Gemini không tồn tại với key này — kiểm tra GEMINI_MODEL trong .env. ({err[:120]})"
    if "api key" in e or "api_key" in e or "401" in e or "403" in e or "permission" in e:
        return "GEMINI_API_KEY không hợp lệ hoặc chưa được cấp quyền."
    return f"Lỗi gọi Gemini: {err[:200]}"


def _call_gemini(prompt: str, timeout: int = 60, retries: int = 2) -> str:
    """Gọi Gemini. Timeout THẬT (không dùng `with ThreadPoolExecutor` vì __exit__ chờ thread xong ->
    timeout vô tác dụng). 503/timeout: thử lại ngắn; 429 hết quota: dừng ngay (thử lại vô ích);
    404: thử lần lượt các model dự phòng (GEMINI_FALLBACK_MODELS)."""
    import time
    from app.config import GEMINI_FALLBACK_MODELS
    client = _get_model()
    models = list(dict.fromkeys([GEMINI_MODEL] + GEMINI_FALLBACK_MODELS))
    last_err = "không rõ"
    for model in models:
        for attempt in range(retries):
            ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            fut = ex.submit(lambda m=model: client.models.generate_content(model=m, contents=prompt))
            try:
                resp = fut.result(timeout=timeout)
                text = (resp.text or "").strip()
                if not text:
                    raise GeminiUnavailable("Gemini trả về nội dung rỗng (có thể bị bộ lọc an toàn chặn).")
                return text
            except concurrent.futures.TimeoutError:
                last_err = f"timeout {timeout}s"
                if attempt < retries - 1:
                    continue
            except GeminiUnavailable:
                raise
            except Exception as e:
                last_err = str(e)
                low = last_err.lower()
                if "429" in low or "resource_exhausted" in low or "quota" in low:
                    raise GeminiUnavailable(_friendly(last_err))
                if "404" in low or "not_found" in low or "not found" in low:
                    break                                   # thử model kế tiếp
                if ("503" in low or "unavailable" in low or "overloaded" in low) and attempt < retries - 1:
                    time.sleep(3)
                    continue
                raise GeminiUnavailable(_friendly(last_err))
            finally:
                ex.shutdown(wait=False)
    raise GeminiUnavailable(_friendly(last_err))


def classify_sentiment_gemini(text: str) -> dict:
    """Không tự cache ở đây — sentiment.classify() đã cache toàn bộ kết quả
    (Gemini hay lexicon) theo CACHE_TTL_SENTIMENT."""
    prompt = (
        "Bạn là chuyên gia phân tích tài chính Việt Nam. Phân loại sentiment của "
        "tiêu đề tin tức chứng khoán sau vào ĐÚNG 1 nhãn: Tích cực, Tiêu cực, hoặc "
        "Trung lập. Chỉ trả về JSON hợp lệ, không thêm chữ nào khác:\n"
        '{"label": "Tích cực" | "Tiêu cực" | "Trung lập", "score": <số từ -1 đến 1>}\n\n'
        f"Tiêu đề: {text}"
    )
    raw = _call_gemini(prompt).strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(raw)
        label = data.get("label", "Trung lập")
        score = float(data.get("score", 0))
    except Exception:
        raise GeminiUnavailable(f"Không parse được JSON từ Gemini: {raw[:200]}")
    if label not in ("Tích cực", "Tiêu cực", "Trung lập"):
        label = "Trung lập"
    return dict(label=label, score=round(max(-1.0, min(1.0, score)), 2),
                matched_positive=[], matched_negative=[], engine="gemini")


@cached(gemini_cache, "recommendation")
def generate_recommendation_gemini(ticker: str, technical_signal: dict, news_titles: list[str],
                                    sentiment_net: float) -> str:
    news_block = "\n".join(f"- {t}" for t in news_titles[:8]) or "(không có tin tức gần đây)"
    prompt = (
        "Bạn là chuyên gia phân tích chứng khoán Việt Nam, viết khuyến nghị đầu tư "
        "NGẮN GỌN (4-6 câu), bằng tiếng Việt, dựa CHỈ trên dữ liệu dưới đây — không "
        "bịa thêm số liệu. Nêu rõ: nhận định xu hướng, điểm cần lưu ý từ tin tức, và "
        "một khuyến nghị hành động thận trọng kèm câu miễn trừ trách nhiệm ở cuối.\n\n"
        f"Mã cổ phiếu: {ticker}\n"
        f"Tín hiệu kỹ thuật: {technical_signal.get('trend')} (điểm {technical_signal.get('score')})\n"
        f"Chi tiết kỹ thuật: {'; '.join(technical_signal.get('detail', []))}\n"
        f"Sentiment tin tức (net, -1 đến 1): {sentiment_net}\n"
        f"Tin tức gần đây:\n{news_block}"
    )
    return _call_gemini(prompt, timeout=25)


def _parse_json(raw: str):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    return json.loads(raw)


def classify_sentiment_batch_gemini(titles: list[str]) -> list[dict]:
    """Phân loại NHIỀU tiêu đề trong 1 lần gọi (thay vì mỗi tiêu đề 1 lần gọi -> chậm + tốn quota)."""
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(titles))
    prompt = (
        "Bạn là chuyên gia phân tích tài chính Việt Nam. Phân loại sentiment từng tiêu đề tin chứng khoán "
        "sau (Tích cực / Tiêu cực / Trung lập). Chỉ trả về JSON hợp lệ dạng mảng, đúng thứ tự, mỗi phần tử "
        '{"label": "...", "score": <-1..1>}, không thêm chữ nào khác.\n\n' + numbered
    )
    data = _parse_json(_call_gemini(prompt, timeout=25))
    if not isinstance(data, list) or len(data) != len(titles):
        raise GeminiUnavailable("Gemini trả về số phần tử không khớp")
    out = []
    for d in data:
        label = d.get("label", "Trung lập")
        if label not in ("Tích cực", "Tiêu cực", "Trung lập"):
            label = "Trung lập"
        out.append(dict(label=label, score=round(max(-1.0, min(1.0, float(d.get("score", 0)))), 2),
                        matched_positive=[], matched_negative=[], engine="gemini"))
    return out


@cached(gemini_cache, "fund_report")
def generate_fundamental_report_gemini(ticker: str, facts_json: str) -> dict:
    """Viết báo cáo phân tích (luận điểm / điểm mạnh / điểm yếu / rủi ro) CHỈ dựa trên số liệu đã tính."""
    prompt = (
        "Bạn là chuyên viên phân tích chứng khoán Việt Nam. Viết báo cáo phân tích sơ bộ cho mã "
        f"{ticker} bằng tiếng Việt, CHỈ dựa trên số liệu JSON bên dưới, tuyệt đối không bịa thêm số. "
        "Trả về JSON hợp lệ (không thêm chữ nào khác) dạng: "
        '{"thesis": "luận điểm đầu tư 3-4 câu", "strengths": ["..."], "weaknesses": ["..."], '
        '"risks": ["..."], "conclusion": "kết luận 1-2 câu kèm miễn trừ trách nhiệm"}. '
        "Mỗi danh sách 3-4 ý ngắn.\n\nSố liệu:\n" + facts_json
    )
    return _parse_json(_call_gemini(prompt, timeout=40))
