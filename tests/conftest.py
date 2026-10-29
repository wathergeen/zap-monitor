import procrastinate.jobs
import procrastinate.tasks
import pytest
from procrastinate.contrib.django import app


@pytest.fixture(autouse=True)
def eager_procrastinate_tasks(monkeypatch):
    """
    Executa tarefas do Procrastinate de forma síncrona durante os testes automatizados,
    garantindo que chamadas a task.defer() ou configure().defer() processem no banco de testes imediatamente.
    """

    def sync_task_defer(self, **task_kwargs):
        return self(**task_kwargs)

    def sync_job_defer(self, **task_kwargs):
        task_name = self.job.task_name
        task = app.tasks.get(task_name)
        merged_kwargs = {**self.job.task_kwargs, **task_kwargs}
        if task:
            return task(**merged_kwargs)
        return 1

    monkeypatch.setattr(procrastinate.tasks.Task, "defer", sync_task_defer)
    monkeypatch.setattr(procrastinate.jobs.JobDeferrer, "defer", sync_job_defer)
