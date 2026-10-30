from django.urls import path
from . import views

app_name = 'comments'

urlpatterns = [
    path('', views.index, name='index'),
    path('comments/company', views.analyze, name='analyze'),
    path('comments/<int:pk>/summary/', views.summary, name='summary'),
]