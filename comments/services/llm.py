import json
import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / '.env', encoding='utf-8-sig')

GMS_URL = 'https://gms.ssafy.io/gmsapi/api.openai.com/v1/chat/completions'
MODEL = 'gpt-5.4-nano'
TIMEOUT = 90


def _chat(prompt, system='너는 한국어 텍스트 처리 도우미야.'):
    key = os.environ.get('GMS_API_KEY')
    if not key:
        raise RuntimeError('환경변수 GMS_API_KEY가 설정되지 않았습니다.')
    res = requests.post(
        GMS_URL,
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        json={
            'model': MODEL,
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': prompt},
            ],
        },
        timeout=TIMEOUT,
    )
    res.raise_for_status()
    return res.json()['choices'][0]['message']['content']


def _parse_json_array(text):
    """LLM이 ```json 펜스나 설명을 덧붙여도 배열만 추출."""
    m = re.search(r'\[.*\]', text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None


def filter_inappropriate(comments):
    """부적절 댓글(욕설·혐오·비방·선정성·과도한 비난)을 제거한 리스트 반환."""
    if not comments:
        return []
    numbered = '\n'.join(f'{i}. {c}' for i, c in enumerate(comments))
    prompt = (
        '다음 댓글 목록에서 부적절한 댓글(욕설, 혐오, 비방, 선정성, 과도한 비난 등)의 '
        '번호(0부터 시작)만 JSON 배열로 알려줘. 없으면 []만 출력해.\n\n' + numbered
    )
    bad = _parse_json_array(_chat(prompt))
    if bad is None:                      
        return comments
    bad_set = {i for i in bad if isinstance(i, int)}
    return [c for i, c in enumerate(comments) if i not in bad_set]


def augment(comments, retries=2):
    """증강 문장 리스트만 반환 (기존 호출부 호환용)."""
    return [text for _, text in augment_indexed(comments, retries)]


def augment_indexed(comments, retries=2):
    """각 댓글을 의미는 유지하되 확장. [(원본 인덱스, 증강 문장), ...] 반환.
    번호로 매칭하므로 개수가 달라도 안전하고, 어떤 정제 댓글의 증강인지 알 수 있다.
    실패한 항목만 최대 retries번 재시도하고, 끝내 못 만든 항목은 결과에서 제외한다."""
    if not comments:
        return []

    results = {}                            
    pending = list(range(len(comments)))      
    last_raw = ''

    for _ in range(retries + 1):
        if not pending:
            break
        numbered = '\n'.join(f'{i}. {comments[i]}' for i in pending)
        prompt = (
            '다음 주식 커뮤니티 댓글들을 각각 원래 의미와 뉘앙스를 유지하면서 '
            '자연스럽게 한두 문장으로 확장해줘. 새로운 사실이나 수치는 추가하지 마. '
            '원문에 없는 조언, 해석, 감정을 새로 만들지 말고, '
            '같은 말을 다른 표현으로 풀어서 써줘. '
            '출력은 JSON 배열만, 각 원소는 {"i": 댓글 번호, "text": 확장한 문장} 형태로. '
            '설명이나 코드블록 없이 배열만 출력해.\n\n' + numbered
        )
        last_raw = _chat(prompt)
        arr = _parse_json_array(last_raw)
        if arr is None:
            continue
        for item in arr:
            if (isinstance(item, dict) and isinstance(item.get('i'), int)
                    and item['i'] in pending and item.get('text')):
                results[item['i']] = str(item['text']).strip()
        pending = [i for i in pending if i not in results]

    if not results:
        raise ValueError(f'증강 결과를 파싱하지 못했습니다. 응답 일부: {last_raw[:300]!r}')
    return [(i, results[i]) for i in sorted(results)]


def summarize(comments):
    """전체 댓글의 분위기와 주요 의견을 3~4문장으로 요약 (F610)."""
    if not comments:
        return ''
    body = '\n'.join(f'- {c}' for c in comments)
    prompt = (
        '다음은 한 종목의 커뮤니티 댓글들이야. 전체 분위기와 주요 의견을 '
        '3~4문장으로 요약해줘. 투자 조언은 하지 마. '
        '마크다운 서식(별표, 제목 기호, 목록 기호) 없이 일반 문장으로만 써.\n\n' + body
    )
    text = _chat(prompt)
    return text.replace('**', '').strip()