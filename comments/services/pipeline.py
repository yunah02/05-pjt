"""크롤링 → 전처리 → 증강 → 저장 파이프라인.

run_pipeline(company, emit) 하나로 전체 흐름을 실행하고,
단계마다 emit(이벤트 dict)를 호출해 진행 상황을 알린다.

이벤트 종류
  {"type": "stage", "code": "F604", "state": "running", "count": 7, "desc": "7/20 수집 중"}
  {"type": "log",   "t": "12.3s", "tag": "ok", "label": "완료", "msg": "..."}
  {"type": "error", "message": "..."}
  {"type": "done",  "url": "/comments/3/", "pk": 3}

- 스트리밍 뷰는 이 이벤트를 한 줄씩(JSON Lines) 브라우저로 보낸다.
- 일반 뷰(JS 미사용 시)는 이벤트를 모아 두었다가 결과만 사용한다.
"""
import json
import logging
import queue
import threading
import time

from django.db import connections
from django.urls import reverse

from ..models import CommentAnalysis
from . import llm
from .crawler import TARGET_COUNT, CompanyNotFound, NoComments, crawl_comments
from .preprocess import calc_iqr, clean_text, rule_based_clean

logger = logging.getLogger(__name__)

# (요구사항 번호, 이름, 색 톤, 대기 상태 설명)
STAGE_DEFS = [
    ('F603', '검색 · 선택', 'base', '최상단 종목 자동 선택'),
    ('F604', '댓글 수집', 'base', f'커뮤니티 댓글 {TARGET_COUNT}개'),
    ('F605', '전처리', 'blue', '길이 · 특수문자 · AI 필터'),
    ('F606', '증강', 'accent', '의미 유지 확장'),
    ('F607', '통합 저장', 'accent', '정제 + 증강 → DB'),
]
STATE_LABELS = {'idle': '대기', 'running': '진행 중', 'done': '완료', 'error': '실패'}
TAG_LABELS = {'info': '진행', 'ok': '완료', 'db': '저장', 'err': '오류'}


# ---------------------------------------------------------------- 단계 카드

def idle_stages():
    """아무것도 실행하지 않은 상태의 단계 카드 목록."""
    return [{
        'code': code, 'label': label, 'tone': tone, 'desc': desc,
        'count': '—', 'state': 'idle', 'state_label': STATE_LABELS['idle'],
        'is_text': code == 'F603',          # F603 카드는 숫자 대신 종목명을 보여준다
    } for code, label, tone, desc in STAGE_DEFS]


def apply_stage_event(stages, event):
    """stage 이벤트 하나를 단계 카드 목록에 반영한다."""
    for s in stages:
        if s['code'] == event['code']:
            s['state'] = event['state']
            s['state_label'] = STATE_LABELS[event['state']]
            if event.get('count') is not None:
                s['count'] = event['count']
            if event.get('desc'):
                s['desc'] = event['desc']
    return stages


def stages_from_analysis(a):
    """DB에 저장된 분석 결과로 '모두 완료' 상태의 단계 카드를 만든다."""
    raw, cleaned, aug = a.raw_comments, a.cleaned_comments, a.augmented_comments
    final_count = len(a.final_comments) or (len(cleaned) + len(aug))
    values = {
        'F603': (a.stock_name, f'"{a.query}" 검색 → 최상단 선택'),
        'F604': (len(raw), '토스증권 커뮤니티 댓글'),
        'F605': (len(cleaned), f'{len(raw)} → {len(cleaned)} (제거 {len(raw) - len(cleaned)})'),
        'F606': (len(aug), f'정제 {len(cleaned)}개 중 {len(aug)}개 확장'),
        'F607': (final_count, f'정제 {len(cleaned)} + 증강 {len(aug)}'),
    }
    stages = idle_stages()
    for s in stages:
        s['count'], s['desc'] = values[s['code']]
        s['state'], s['state_label'] = 'done', STATE_LABELS['done']
    return stages


# ---------------------------------------------------------------- 화면용 데이터

def raw_items(a):
    """원본 댓글마다 전처리 후 남았는지(kept) 표시."""
    cleaned_set = set(a.cleaned_comments)
    return [{'text': r, 'kept': clean_text(r) in cleaned_set} for r in a.raw_comments]


def final_pairs(a):
    """최종 통합 결과를 [{n, clean, aug}] 짝으로 묶는다."""
    if a.final_comments:
        pairs = {}
        for item in a.final_comments:
            p = pairs.setdefault(item['n'], {'n': item['n'], 'clean': '', 'aug': ''})
            p[item['src']] = item['text']
        return [pairs[n] for n in sorted(pairs)]
    # final_comments 칸이 생기기 전 저장된 기록: 순서대로 짝짓는다
    aug = a.augmented_comments
    return [{'n': i + 1, 'clean': c, 'aug': aug[i] if i < len(aug) else ''}
            for i, c in enumerate(a.cleaned_comments)]


def build_final(cleaned, indexed_aug):
    """정제 댓글 바로 뒤에 그 댓글의 증강 문장을 붙여 하나의 결과 집합을 만든다 (F607)."""
    aug_map = dict(indexed_aug)
    final = []
    for i, c in enumerate(cleaned):
        final.append({'n': i + 1, 'src': 'clean', 'text': c})
        if i in aug_map:
            final.append({'n': i + 1, 'src': 'aug', 'text': aug_map[i]})
    return final


# ---------------------------------------------------------------- 파이프라인

class _Run:
    def __init__(self, emit):
        self.emit = emit
        self.t0 = time.perf_counter()
        self.logs = []

    def log(self, tag, msg):
        entry = {'t': f'{time.perf_counter() - self.t0:.1f}s', 'tag': tag,
                 'label': TAG_LABELS[tag], 'msg': msg}
        self.logs.append(entry)
        self.emit({'type': 'log', **entry})

    def stage(self, code, state, count=None, desc=None):
        event = {'type': 'stage', 'code': code, 'state': state,
                 'state_label': STATE_LABELS[state]}
        if count is not None:
            event['count'] = count
        if desc:
            event['desc'] = desc
        self.emit(event)

    def fail(self, code, message):
        self.stage(code, 'error', desc='이 단계에서 멈춤 — 로그 확인')
        self.log('err', message)
        self.emit({'type': 'error', 'message': message})
        return None


def run_pipeline(company, emit):
    """전체 파이프라인 실행. 성공하면 CommentAnalysis, 실패하면 None을 반환한다."""
    run = _Run(emit)
    run.log('info', f'검색어 입력: {company}')
    run.stage('F603', 'running', desc='토스증권에서 검색 중')

    step = {'code': 'F603', 'last_n': -1}

    def on_progress(event, **data):
        if event == 'driver':
            run.log('info', 'Chrome 브라우저 시작')
        elif event == 'search':
            run.log('info', '토스증권 검색창에 입력')
        elif event == 'selected':
            run.log('ok', f'최상단 종목 선택 ({data.get("code") or "?"})')
        elif event == 'stock':
            name = data['name']
            run.stage('F603', 'done', count=name, desc=f'"{company}" → {name}')
            run.log('info', f'{name} 커뮤니티 탭으로 이동')
            step['code'] = 'F604'
            run.stage('F604', 'running', count=0, desc=f'0/{TARGET_COUNT} 수집 중')
        elif event == 'collect':
            n = data['n']
            if n != step['last_n']:
                step['last_n'] = n
                run.stage('F604', 'running', count=n, desc=f'{n}/{TARGET_COUNT} 수집 중')

    # F603 · F604: 검색 + 수집
    try:
        stock_name, raw = crawl_comments(company, on_progress=on_progress)
    except CompanyNotFound:                                               # F611
        return run.fail('F603', f"'{company}'에 해당하는 회사를 찾을 수 없습니다.")
    except NoComments:                                                    # F611
        return run.fail('F604', '해당 종목의 커뮤니티 댓글이 없습니다.')
    except Exception:
        logger.exception('크롤링 실패')
        return run.fail(step['code'], '데이터를 수집하는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.')

    run.stage('F604', 'done', count=len(raw), desc='토스증권 커뮤니티 댓글')
    run.log('ok', f'댓글 {len(raw)}개 수집')

    # F605 · F606: 전처리 + 증강
    try:
        step['code'] = 'F605'
        run.stage('F605', 'running', desc='규칙 기반 정제 중')
        iqr_info = calc_iqr(raw)
        rule_cleaned = rule_based_clean(raw)
        run.log('ok', f'규칙 전처리 {len(raw)} → {len(rule_cleaned)}')

        run.stage('F605', 'running', count=len(rule_cleaned), desc='AI 부적절 댓글 필터 중')
        cleaned = llm.filter_inappropriate(rule_cleaned)
        run.log('ok', f'AI 필터 {len(rule_cleaned)} → {len(cleaned)}')
        run.stage('F605', 'done', count=len(cleaned),
                  desc=f'{len(raw)} → {len(cleaned)} (제거 {len(raw) - len(cleaned)})')

        step['code'] = 'F606'
        run.stage('F606', 'running', desc=f'{len(cleaned)}개 확장 중 (AI)')
        indexed_aug = llm.augment_indexed(cleaned) if cleaned else []
        run.log('ok', f'증강 {len(indexed_aug)}/{len(cleaned)} 생성')
        run.stage('F606', 'done', count=len(indexed_aug),
                  desc=f'정제 {len(cleaned)}개 중 {len(indexed_aug)}개 확장')
    except Exception:
        logger.exception('LLM 처리 실패')
        return run.fail(step['code'], 'AI 처리 중 오류가 발생했습니다. API 키/네트워크를 확인해 주세요.')

    # F602 · F607: 통합 + DB 저장
    step['code'] = 'F607'
    run.stage('F607', 'running', desc='DB 저장 중')
    augmented = [text for _, text in indexed_aug]
    final = build_final(cleaned, indexed_aug)
    try:
        analysis = CommentAnalysis.objects.create(
            query=company, stock_name=stock_name, raw_comments=raw,
            cleaned_comments=cleaned, augmented_comments=augmented,
            final_comments=final, iqr_info=iqr_info,
        )
        run.log('db', f'분석 결과 #{analysis.pk} 저장')
        analysis.logs = run.logs
        analysis.save(update_fields=['logs'])
    except Exception:
        logger.exception('DB 저장 실패')
        return run.fail('F607', 'DB 저장 중 오류가 발생했습니다. migrate를 했는지 확인해 주세요.')

    run.stage('F607', 'done', count=len(final), desc=f'정제 {len(cleaned)} + 증강 {len(augmented)}')
    emit({'type': 'done', 'pk': analysis.pk,
          'url': reverse('comments:detail', args=[analysis.pk])})
    return analysis


# ---------------------------------------------------------------- 스트리밍

def stream_events(company, keepalive_sec=10):
    """파이프라인을 별도 스레드에서 실행하고, 이벤트를 JSON 한 줄씩 내보내는 제너레이터.

    Selenium/LLM 호출은 오래 걸리는 '막히는' 작업이라 yield를 중간에 넣기 어렵다.
    그래서 작업 스레드가 이벤트를 큐에 넣고, 이 제너레이터는 큐에서 꺼내 바로 보낸다.
    """
    events = queue.Queue()
    finished = object()

    def worker():
        try:
            run_pipeline(company, events.put)
        except Exception:
            logger.exception('파이프라인 실패')
            events.put({'type': 'error', 'message': '알 수 없는 오류가 발생했습니다.'})
        finally:
            connections.close_all()          # 스레드에서 연 DB 연결 정리
            events.put(finished)

    threading.Thread(target=worker, daemon=True).start()

    while True:
        try:
            event = events.get(timeout=keepalive_sec)
        except queue.Empty:
            yield '\n'                       # 연결 유지용 빈 줄 (브라우저는 무시)
            continue
        if event is finished:
            break
        yield json.dumps(event, ensure_ascii=False) + '\n'