from zerino.publishing.job_events import JobEventStore
from zerino.publishing.scheduled_events import SqliteScheduledStore

job_events = JobEventStore()
scheduled_events = SqliteScheduledStore()