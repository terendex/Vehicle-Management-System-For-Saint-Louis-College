from django.contrib import admin
from .models import Office, VisitorPass, AccessLog
admin.site.register(Office)
admin.site.register(VisitorPass)
admin.site.register(AccessLog)