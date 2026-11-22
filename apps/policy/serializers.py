from rest_framework import serializers


class PolicyVersionSerializer(serializers.Serializer):
    number = serializers.IntegerField()
    text = serializers.CharField()
    published_at = serializers.DateTimeField()


class PolicyAcceptanceSerializer(serializers.Serializer):
    version = serializers.IntegerField()
    accepted_at = serializers.DateTimeField()
