from django.apps import AppConfig


class TenasapoKnowledgeConfig(AppConfig):
    name = 'tenasapo_knowledge'

    def ready(self):
        from . import signals
