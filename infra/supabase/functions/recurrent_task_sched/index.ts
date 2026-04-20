import { serve } from "https://deno.land/std@0.168.0/http/server.ts"
import { createClient } from "https://esm.sh/@supabase/supabase-js@2"
import { rrulestr } from "https://esm.sh/rrule"

serve(async (req) => {
  const { dry_run } = await req.json()
  
  const supabase = createClient(
    Deno.env.get('SB_API_URL') ?? '',
    Deno.env.get('SB_SECRET_DEFAULT_KEY') ?? ''
  )

  // 1. Buscamos tareas activas con regla de recurrencia
  const { data: tasks } = await supabase
    .from('tasks')
    .select('*')
    .eq('is_active', true)
    .not('rrule', 'is', null)

  const results = []

  for (const task of tasks || []) {
    const rule = rrulestr(task.rrule)
    const nextDates = rule.all((date) => date > new Date(), { limit: 3 })

    if (nextDates.length > 0) {
      if (!dry_run) {
        // 2. Insertamos las próximas 3 instancias
        for (const date of nextDates) {
          await supabase.from('task_instances').insert({
            task_id: task.id,
            start_date: date,
            status: 'pending'
          })
        }
      }
      results.push({ task: task.id, scheduled: nextDates.length })
    } else {
      // 3. Si no hay más fechas, desactivamos la tarea
      if (!dry_run) {
        await supabase.from('tasks').update({ is_active: false }).eq('id', task.id)
      }
    }
  }

  return new Response(JSON.stringify({ message: "Cron executed", results }), {
    headers: { "Content-Type": "application/json" },
  })
})