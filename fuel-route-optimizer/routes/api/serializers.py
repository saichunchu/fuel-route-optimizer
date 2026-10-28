from rest_framework import serializers

class CoordinateSerializer(serializers.Serializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)

class OptimizeRouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(required=False, allow_blank=False, max_length=200)
    finish = serializers.CharField(required=False, allow_blank=False, max_length=200)
    start_coordinates = CoordinateSerializer(required=False)
    finish_coordinates = CoordinateSerializer(required=False)

    def validate(self, attrs):
        has_start_text = bool(attrs.get("start"))
        has_finish_text = bool(attrs.get("finish"))
        has_start_coords = bool(attrs.get("start_coordinates"))
        has_finish_coords = bool(attrs.get("finish_coordinates"))

        if has_start_text == has_start_coords:
            raise serializers.ValidationError(
                "Provide exactly one of 'start' or 'start_coordinates'."
            )
        if has_finish_text == has_finish_coords:
            raise serializers.ValidationError(
                "Provide exactly one of 'finish' or 'finish_coordinates'."
            )
        return attrs
