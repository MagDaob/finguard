CREATE TABLE conversations (
 conversation_ref text PRIMARY KEY,
 subject_ref text NOT NULL REFERENCES principals(subject_ref),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversations_owner ON conversations(subject_ref,created_at DESC);
CREATE TABLE conversation_turns (
 conversation_ref text NOT NULL REFERENCES conversations(conversation_ref),
 sequence integer NOT NULL,
 message_ref text NOT NULL,
 request_digest text NOT NULL,
 user_text text NOT NULL,
 payload jsonb NOT NULL,
 task_ref text REFERENCES tasks(task_ref),
 ready boolean NOT NULL DEFAULT false,
 reply text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(conversation_ref,sequence),
 UNIQUE(conversation_ref,message_ref)
);
