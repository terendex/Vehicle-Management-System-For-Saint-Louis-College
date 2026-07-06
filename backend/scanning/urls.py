from django.urls import path
from . import views
from accounts.views import QRLoginView

urlpatterns = [
    path('',                        views.ScanView.as_view(),              name='scan'),
    path('override/',               views.OverrideEntryView.as_view(),     name='scan-override'),
    path('deny/',                   views.DenyEntryView.as_view(),         name='scan-deny'),
    path('exit/',                   views.ExitLogView.as_view(),           name='scan-exit'),
    path('visitor-pass/',                    views.VisitorPassView.as_view(),       name='visitor-pass'),
    path('visitor-pass/exit-scan/',          views.VisitorQrExitView.as_view(),     name='visitor-exit-scan'),
    path('visitor-pass/<int:pk>/printed/',   views.VisitorPassPrintedView.as_view(), name='visitor-printed'),
    path('visitor-pass/<int:pk>/exit/',      views.ExitScanView.as_view(),          name='visitor-exit'),
    path('visitor-pass/<int:pk>/extend/',    views.ExtendVisitorPassView.as_view(), name='visitor-extend'),
    path('offices/',                views.OfficeListView.as_view(),        name='offices'),
    path('gates/',                  views.GateListView.as_view(),          name='gates'),
    path('gates/<int:pk>/',         views.GateDetailView.as_view(),        name='gate-detail'),
    path('logs/',                   views.AccessLogListView.as_view(),     name='access-logs'),
    path('guard-monitor/',          views.GuardMonitorView.as_view(),      name='guard-monitor'),
    path('ml/samples/',             views.MLTrainingSampleList.as_view(),  name='ml-samples'),
    path('ml/samples/<int:pk>/',    views.MLTrainingSampleReview.as_view(), name='ml-sample-review'),
    path('ml/stats/',               views.MLStatsView.as_view(),           name='ml-stats'),
    path('ml/retrain/',             views.TriggerRetrainView.as_view(),    name='ml-retrain'),
    path('test-rtsp/',              views.TestRtspView.as_view(),          name='test-rtsp'),
    # Dual-gate system
    path('manual-entry/',           views.ManualEntryView.as_view(),       name='manual-entry'),
    path('qr-login/',               QRLoginView.as_view(),                 name='qr-login'),
    path('current-shifts/',         views.CurrentShiftsView.as_view(),     name='current-shifts'),
    path('shifts/',                 views.GuardShiftListView.as_view(),    name='guard-shifts'),
]
