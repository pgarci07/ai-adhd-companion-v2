CREATE OR REPLACE FUNCTION fn_create_default_list_for_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.lists (user_id, name, description)
    VALUES (
        NEW.id, -- En Supabase, el ID del perfil suele ser el mismo UUID del usuario
        'my list',
        'you default list; feel free to change the name and description any time'
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_create_default_list
AFTER INSERT ON public.profiles
FOR EACH ROW
EXECUTE FUNCTION fn_create_default_list_for_new_user();
