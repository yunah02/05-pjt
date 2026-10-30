import re
import statistics

MIN_LEN, MAX_LEN = 5, 76


def calc_iqr(comments):
    """원본 댓글 길이 분포의 IQR 임계값 (F602 저장용)."""
    lengths = [len(c) for c in comments]
    if len(lengths) < 4:
        return {}
    q1, _, q3 = statistics.quantiles(lengths, n=4)
    iqr = q3 - q1
    return {
        'q1': q1, 'q3': q3, 'iqr': iqr,
        'lower': q1 - 1.5 * iqr, 'upper': q3 + 1.5 * iqr,
    }


def clean_text(text):
    text = re.sub(r'[ㄱ-ㅎㅏ-ㅣ]+', ' ', text)                    # ㅋㅋㅋ, ㅠㅠ 등
    text = re.sub(r'[^\w\s.,!?%~가-힣]', ' ', text)               # 특수문자·이모지
    text = re.sub(r'([!?.~,])(\s*\1)+', r'\1', text)             # 반복 문장부호 → 1개
    text = re.sub(r'\s+', ' ', text).strip()                      # 중복 공백
    return text


def rule_based_clean(comments):
    """결측치 제거 → 정제 → 길이 필터(5~76)."""
    result = []
    for c in comments:
        if not c:
            continue
        cleaned = clean_text(c)
        if MIN_LEN <= len(cleaned) <= MAX_LEN:
            result.append(cleaned)
    return result