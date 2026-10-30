import logging

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import CommentAnalysis
from .services import llm
from .services.crawler import CompanyNotFound, NoComments, crawl_comments
from .services.preprocess import calc_iqr, rule_based_clean

logger = logging.getLogger(__name__)


def index(request):
    return render(request, 'comments/index.html')


def analyze(request):
    company = request.GET.get('company_name', '').strip()
    if not company:
        return redirect('comments:index')

    print(company)   

    try:
        stock_name, raw = crawl_comments(company)        
    except CompanyNotFound:                           
        return render(request, 'comments/index.html', {
            'error': f"'{company}'에 해당하는 회사를 찾을 수 없습니다."})
    except NoComments:                                  
        return render(request, 'comments/index.html', {
            'error': '해당 종목의 커뮤니티 댓글이 없습니다.'})
    except Exception:
        logger.exception('크롤링 실패')
        return render(request, 'comments/index.html', {
            'error': '데이터를 수집하는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.'})

    try:
        iqr_info = calc_iqr(raw)
        cleaned = llm.filter_inappropriate(rule_based_clean(raw))   
        augmented = llm.augment(cleaned)                           
    except Exception:
        logger.exception('LLM 처리 실패')
        return render(request, 'comments/index.html', {
            'error': 'AI 처리 중 오류가 발생했습니다. API 키/네트워크를 확인해 주세요.'})

    analysis = CommentAnalysis.objects.create(                     
        query=company, stock_name=stock_name, raw_comments=raw,
        cleaned_comments=cleaned, augmented_comments=augmented,
        iqr_info=iqr_info,
    )
    return render(request, 'comments/index.html', {
        'analysis': analysis,
        'final': cleaned + augmented,     
        'company': company,
    })


@require_POST
def summary(request, pk):                                          
    analysis = get_object_or_404(CommentAnalysis, pk=pk)
    try:
        analysis.summary = llm.summarize(analysis.raw_comments)
        analysis.save(update_fields=['summary'])
    except Exception:
        logger.exception('요약 실패')
        return JsonResponse({'error': '요약 생성에 실패했습니다.'}, status=500)
    return JsonResponse({'summary': analysis.summary})