import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()

from rest_framework.test import APIRequestFactory, force_authenticate
from apps.cajas.views import DashboardCajasView
from django.contrib.auth import get_user_model

factory = APIRequestFactory()
request = factory.get('/api/cajas/dashboard/')
User = get_user_model()
user = User.objects.first()
force_authenticate(request, user=user)

view = DashboardCajasView.as_view()
try:
    response = view(request)
    print("Response Status:", response.status_code)
    print("Response Data:", response.data)
except Exception as e:
    import traceback
    traceback.print_exc()
