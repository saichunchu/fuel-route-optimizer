import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from routes.api.serializers import OptimizeRouteRequestSerializer
from routes.services.errors import (
    FuelDataError, RouteProviderError, RouteInfeasibleError, AddressResolutionError
)
from routes.services.planner import plan_fuel_route

logger = logging.getLogger(__name__)

class HealthView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({"status": "ok", "service": "fuel-route-optimizer"})

class OptimizeFuelRouteView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = OptimizeRouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = plan_fuel_route(serializer.validated_data)
            return Response(result, status=status.HTTP_200_OK)
        except RouteInfeasibleError as exc:
            return Response({
                "status": "infeasible",
                "error": {"code": "NO_FEASIBLE_FUEL_PLAN", "message": str(exc)},
            }, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except AddressResolutionError as exc:
            return Response({
                "status": "error",
                "error": {"code": "ADDRESS_RESOLUTION_FAILED", "message": str(exc)},
            }, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except FuelDataError as exc:
            logger.exception("Fuel dataset error")
            return Response({
                "status": "error",
                "error": {"code": "FUEL_DATA_UNAVAILABLE", "message": str(exc)},
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except RouteProviderError as exc:
            logger.warning("Routing provider error: %s", exc)
            return Response({
                "status": "error",
                "error": {"code": "ROUTING_PROVIDER_ERROR", "message": str(exc)},
            }, status=status.HTTP_502_BAD_GATEWAY)
        except Exception:
            logger.exception("Unexpected route planning failure")
            return Response({
                "status": "error",
                "error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred."},
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
