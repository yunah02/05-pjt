from django.db import models


class CommentAnalysis(models.Model):
    query = models.CharField(max_length=100)                      
    stock_name = models.CharField(max_length=100)               
    raw_comments = models.JSONField(default=list)               
    cleaned_comments = models.JSONField(default=list)              
    augmented_comments = models.JSONField(default=list)          
    final_comments = models.JSONField(default=list, blank=True)    
    iqr_info = models.JSONField(default=dict)                      
    summary = models.TextField(blank=True, default='')             
    logs = models.JSONField(default=list, blank=True)             
    created_at = models.DateTimeField(auto_now_add=True)           

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.stock_name} ({self.created_at:%Y-%m-%d %H:%M})'