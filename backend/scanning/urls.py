from django.urls import path
from . import views

urlpatterns = [
    path('',                        views.ScanView.as_view(),              name='scan'),
    path('override/',               views.OverrideEntryView.as_view(),     name='scan-override'),
    path('exit/',                   views.ExitLogView.as_view(),           name='scan-exit'),
    path('visitor-pass/',                    views.VisitorPassView.as_view(),       name='visitor-pass'),
    path('visitor-pass/<int:pk>/exit/',      views.ExitScanView.as_view(),          name='visitor-exit'),
    path('visitor-pass/<int:pk>/extend/',    views.ExtendVisitorPassView.as_view(), name='visitor-extend'),
    path('offices/',                views.OfficeListView.as_view(),        name='offices'),
    path('logs/',                   views.AccessLogListView.as_view(),     name='access-logs'),
    path('guard-monitor/',          views.GuardMonitorView.as_view(),      name='guard-monitor'),
    path('ml/samples/',             views.MLTrainingSampleList.as_view(),  name='ml-samples'),
    path('ml/samples/<int:pk>/',    views.MLTrainingSampleReview.as_view(), name='ml-sample-review'),
    path('ml/stats/',               views.MLStatsView.as_view(),           name='ml-stats'),
    path('ml/retrain/',             views.TriggerRetrainView.as_view(),    name='ml-retrain'),
    path('test-rtsp/',              views.TestRtspView.as_view(),          name='test-rtsp'),
]
