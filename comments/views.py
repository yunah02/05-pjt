import logging

from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import CommentAnalysis
from .services import llm
from .services.pipeline import (
    apply_stage_event, final_pairs, idle_stages, raw_items, run_pipeline,
    stages_from_analysis, stream_events,
)
from .services.preprocess import MAX_LEN, MIN_LEN

logger = logging.getLogger(__name__)

TEMPLATE = 'comments/index.html'


def _ctx(**extra):
    ctx = {'min_len': MIN_LEN, 'max_len': MAX_LEN, 'stages': idle_stages()}
    ctx.update(extra)
    return ctx


def _company(request):
    return request.GET.get('company_name', '').strip()


def index(request):
    return render(request, TEMPLATE, _ctx())


def analyze_stream(request):
    """실시간 진행 상황: 이벤트를 JSON 한 줄씩 계속 보낸다 (JS fetch로 읽음)."""
    company = _company(request)
    if not company:                                    # F601: 빈 값이면 아무 동작도 하지 않음
        return JsonResponse({'error': '회사명을 입력해 주세요.'}, status=400)

    print(company)                                     # F601: 터미널에 회사명 출력

    response = StreamingHttpResponse(stream_events(company),
                                     content_type='application/x-ndjson; charset=utf-8')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


def analyze(request):
    """JS가 꺼져 있을 때의 일반 제출: 끝까지 실행한 뒤 결과 화면으로 이동."""
    company = _company(request)
    if not company:                                    # F601
        return redirect('comments:index')

    print(company)                                     # F601

    events = []
    analysis = run_pipeline(company, events.append)
    if analysis:
        return redirect('comments:detail', pk=analysis.pk)

    # 실패(F611): 어느 단계에서 멈췄는지 카드와 로그로 보여준다
    stages = idle_stages()
    for event in events:
        if event['type'] == 'stage':
            apply_stage_event(stages, event)
    error = next((e['message'] for e in events if e['type'] == 'error'), '오류가 발생했습니다.')
    logs = [e for e in events if e['type'] == 'log']
    return render(request, TEMPLATE, _ctx(company=company, error=error, logs=logs, stages=stages))


def detail(request, pk):
    """저장된 분석 결과 화면."""
    analysis = get_object_or_404(CommentAnalysis, pk=pk)
    return render(request, TEMPLATE, _ctx(
        company=analysis.query,
        analysis=analysis,
        stages=stages_from_analysis(analysis),
        logs=analysis.logs,
        raw_items=raw_items(analysis),
        final_pairs=final_pairs(analysis),
        final_count=len(analysis.final_comments)
                    or len(analysis.cleaned_comments) + len(analysis.augmented_comments),
    ))


@require_POST
def summary(request, pk):                              # F610
    analysis = get_object_or_404(CommentAnalysis, pk=pk)
    try:
        analysis.summary = llm.summarize(analysis.raw_comments)
        analysis.save(update_fields=['summary'])
    except Exception:
        logger.exception('요약 실패')
        return JsonResponse({'error': '요약 생성에 실패했습니다.'}, status=500)
    return JsonResponse({'summary': analysis.summary})