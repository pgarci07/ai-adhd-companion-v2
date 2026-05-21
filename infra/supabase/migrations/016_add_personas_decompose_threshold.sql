ALTER TABLE public.personas
ADD COLUMN IF NOT EXISTS decompose_threshold integer;

COMMENT ON COLUMN public.personas.decompose_threshold IS
'Inclusive WSUB threshold used by Body-Doubling to decide whether OpenAI should decompose a task into microsteps for this persona. Null disables decomposition for the persona.';

UPDATE public.personas
SET decompose_threshold = 7
WHERE lower(name) = 'procrastinator';

UPDATE public.personas
SET decompose_threshold = 6
WHERE lower(name) = 'hyper-focused';

UPDATE public.personas
SET decompose_threshold = 5
WHERE lower(name) = 'overwhelmed planner';
