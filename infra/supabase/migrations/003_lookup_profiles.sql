-- 1. PERSONAS
INSERT INTO personas(name, description, self_describing) VALUES 
    ('Alex', 'The Procastinator',
    $$I often feel a heavy 'friction' when trying to start tasks, even when I know they are important and I really want to get them done.$$),
    ('Maya', 'The Overwhelmed Planner', 
    $$I have a million great ideas and lists, but I get stuck in 'choice paralysis' trying to figure out which one is the right one to do first.$$),
    ('Daniel', 'The Hyperfocus User',
    $$Once I 'lock in' to a task, the rest of the world disappears, making it very difficult for me to stop, eat, or switch gears to what's next.$$);

-- 2. STATES
INSERT INTO states(name, description, self_describing) VALUES 
    ('frozen', 'This is characterized by Executive Paralysis. The student is staring at a screen' || 
    ' or book but cannot move their hands to start. Neurologically, the "cost" of starting feels' ||
    ' physically painful. There is a high presence of the "Wall of Awful"—a barrier of past failures' ||
    ' and anxiety that makes the task look like a threat rather than a goal.',
    'I want to start, but I feel physically stuck and my to-do list feels like a threat.'),
    ('distracted', ' The student has started, but their attention is "leaking." They are prone to' ||
    ' "side-quests"—for example, researching the history of pens while trying to write a history essay.' ||
    ' Time blindness is high here; they feel like they’ve been working for an hour, but only five minutes' || 
    ' have passed on the actual task.',
    $$I'm in motion, but I keep drifting into 'side-quests' and can't stay on the main track.$$),
    ('optimizer', 'The student is in a "High-Performance" mode. They are using Logistical Intelligence' || 
    ' to move tasks around. They are capable of handling "Auto-Chunking" and prioritizing. In this state,' || 
    ' the brain is seeking "completion dopamine," and the student can handle a high density of tasks if the' || 
    ' structure is clear.',
    $$The plan is working, I feel steady momentum, and I'm ready to check things off.$$),
    ('recovery', 'This is a state of Dopamine Bankruptcy. The student has pushed too hard for too long,' || 
    ' often in an Optimizer or Hyper-focused mode. Sensory input (pings, bright screens, cheers) feels' || 
    ' irritating. The "Site Manager" (AI) starts to feel like a "taskmaster" rather than a helper. The goal' || 
    ' here is not productivity, but preservation.',
    'My brain is out of fuel; every notification feels heavy and I need a guilt-free break.');



