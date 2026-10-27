ALTER TABLE tasks ADD COLUMN context_task_ref text REFERENCES tasks(task_ref);
