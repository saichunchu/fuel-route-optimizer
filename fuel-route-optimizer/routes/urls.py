from django.urls import path
from routes.api.views import OptimizeFuelRouteView, HealthView

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("fuel-routes/optimize/", OptimizeFuelRouteView.as_view(), name="optimize-fuel-route"),
]
