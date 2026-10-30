from django.db import models


class CommentAnalysis(models.Model):
    query = models.CharField(max_length=100)              # 사용자가 입력한 검색어
    stock_name = models.CharField(max_length=100)         # 실제 매칭된 종목 공식명
    raw_comments = models.JSONField(default=list)         # 원본 댓글 목록
    cleaned_comments = models.JSONField(default=list)     # 정제 댓글 목록
    augmented_comments = models.JSONField(default=list)   # 증강 댓글 목록
    iqr_info = models.JSONField(default=dict)             # IQR 임계값 정보
    summary = models.TextField(blank=True, default='')    # F610 요약 (심화)
    created_at = models.DateTimeField(auto_now_add=True)  # 생성 일시

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.stock_name} ({self.created_at:%Y-%m-%d %H:%M})'