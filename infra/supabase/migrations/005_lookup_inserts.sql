DELETE FROM auth.users;
DELETE FROM dim_task_consequences;
DELETE FROM dim_task_frictions;
DELETE FROM dim_task_sizes;
DELETE FROM personas;
DELETE FROM states;

INSERT INTO dim_task_consequences (label, self_describing, weight) VALUES
('Invisible', $$No one notices if you don't do it.$$, 1),
('Minor', $$Small annoyance or loss of "bonus" points.$$, 2),
('Social/Financial', $$Someone else is waiting; small late fee; slight grade drop.$$, 3),
('Severe', $$Major grade impact; utility shut-off; professional reprimand.$$, 4),
('Catastrophic', $$Academic failure; loss of job; health crisis; legal trouble.$$, 5);

INSERT INTO dim_task_frictions (label, self_describing, weight) VALUES
('Ace', $$Right up my street. It's fun. You're actually keen, and you feel like you could smash it out in no time.$$, 1),
('Sound', $$Yeah, it's fine. Alright. It feels manageable, fair, and you don't mind getting stuck in.$$, 2),
('Long', $$A proper slog. it's a tedious, multi-step slog that you're already tired of just thinking about. It feels like it will take forever.$$, 3),
('Peak', $$An absolute mission. Inconvenient, frustrating, annoying.$$, 4),
('Grim', $$Pure filth. Feels mentally “sticky” or unpleasant. You'd rather do almost anything else.$$, 5);

INSERT INTO dim_task_sizes (label, self_describing, weight) VALUES
('2–15m', $$Done before the tea's brewed. A “quick win”.$$, 1),
('30m', $$A proper little sprint.$$, 2),
('1h', $$A solid hour's stint. Need's a sit-down.$$, 3),
('3h', $$A right old mission, this. A major chunk of the day.$$, 4),
('8h+', $$A proper mountain to climb. A multi-session marathon.$$, 5);

INSERT INTO personas (name, description, self_describing) VALUES
('Procrastinator', 'Pending', $$I often feel a heavy 'friction' when trying to start tasks, even when I know they are important and I really want to get them done.$$),
('Overwhelmed Planner', 'Pending', $$I have a million great ideas and lists, but I get stuck in 'choice paralysis' trying to figure out which one is the right one to do first.$$),
('Hyper-focused', 'Pending', $$Once I 'lock in' to a task, the rest of the world disappears, making it very difficult for me to stop, eat, or switch gears to what's next.$$);

INSERT INTO states (name, description, self_describing) VALUES
('Frozen', 'Pending', $$I want to start, but I feel physically stuck and my to-do list feels like a threat.$$),
('Distracted', 'Pending', $$I'm in motion, but I keep drifting into 'side-quests' and can't stay on the main track.$$),
('Engaged', 'Pending', $$The plan is working, I feel steady momentum, and I'm ready to check things off $$),
('Recovery', 'Pending', $$My brain is out of fuel; every notification feels heavy and I need a guilt-free break.$$);

