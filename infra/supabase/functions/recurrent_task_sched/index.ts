import { serve } from "https://deno.land/std@0.168.0/http/server.ts"
import { createClient } from "https://esm.sh/@supabase/supabase-js@2"
import { rrulestr } from "https://esm.sh/rrule"

// Keep this at the top so the scheduling target can be changed in one place.
// The worker will try to keep this many future instances per recurring task.
const FUTURE_INSTANCES_BUFFER = 1

// If a ready/open/asleep instance has been overdue for at least this many full
// days, the worker will move it to debt.
const DAYS_OVERDUE_BEFORE_DEBT = 0

// If an instance has remained in debt for at least this many full days, the
// worker will move it to stale.
const DAYS_IN_DEBT_BEFORE_STALE = 1

// Default to safe mode unless the caller explicitly disables dry-run.
const DEFAULT_DRY_RUN = true
const UNAUTHORIZED_RESPONSE = new Response(
  JSON.stringify(
    {
      code: "UNAUTHORIZED",
      message: "Missing or invalid scheduler authorization",
    },
    null,
    2,
  ),
  {
    status: 401,
    headers: { "Content-Type": "application/json" },
  },
)

type TaskRow = {
  id: string
  title: string | null
  rrule: string | null
  is_active: boolean
}

type TaskInstanceRow = {
  id: string
  task_id: string
  parent_instance_id: string | null
  instance_number: number
  start_date: string
  due_date: string
  original_start_date: string | null
  original_due_date: string | null
}

type ScheduleInsertPlan = {
  instanceNumber: number
  startDate: Date
  dueDate: Date
}

type ChildSpawnPlan = {
  parentInstanceId: string
  instanceNumber: number
  startDate: Date
  dueDate: Date
}

type ChildTemplate = {
  firstParentInstanceNumber: number
  offsetMs: number
  durationMs: number
}

function parseRequestBody(rawBody: unknown): { dry_run: boolean } {
  if (!rawBody || typeof rawBody !== "object") {
    return { dry_run: DEFAULT_DRY_RUN }
  }

  const maybeDryRun = (rawBody as Record<string, unknown>).dry_run
  return {
    dry_run: typeof maybeDryRun === "boolean" ? maybeDryRun : DEFAULT_DRY_RUN,
  }
}

function isAuthorizedSchedulerRequest(req: Request): boolean {
  const expectedToken = Deno.env.get("ADHD_COMPANION_KEY") ?? ""
  const authorization = req.headers.get("Authorization") ?? ""
  const bearerPrefix = "Bearer "

  if (!expectedToken || !authorization.startsWith(bearerPrefix)) {
    return false
  }

  return authorization.slice(bearerPrefix.length) === expectedToken
}

function asDate(value: string | null | undefined): Date | null {
  if (!value) {
    return null
  }

  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function asIsoString(value: Date): string {
  return value.toISOString()
}

function pickStartDate(instance: TaskInstanceRow): Date {
  return new Date(instance.start_date)
}

function pickDueDate(instance: TaskInstanceRow): Date {
  return new Date(instance.due_date)
}

function sameInstant(left: Date, right: Date): boolean {
  return left.getTime() === right.getTime()
}

function startOfUtcDay(value: Date): Date {
  return new Date(Date.UTC(
    value.getUTCFullYear(),
    value.getUTCMonth(),
    value.getUTCDate(),
  ))
}

function sortByStartDate(instances: TaskInstanceRow[]): TaskInstanceRow[] {
  return [...instances].sort(
    (left, right) => pickStartDate(left).getTime() - pickStartDate(right).getTime(),
  )
}

async function fetchTaskInstances(
  supabase: ReturnType<typeof createClient>,
  taskId: string,
): Promise<TaskInstanceRow[]> {
  const { data, error } = await supabase
    .from("task_instances")
    .select(
      [
        "id",
        "task_id",
        "parent_instance_id",
        "instance_number",
        "start_date",
        "due_date",
        "original_start_date",
        "original_due_date",
      ].join(", "),
    )
    .eq("task_id", taskId)

  if (error) {
    throw error
  }

  return (data ?? []) as TaskInstanceRow[]
}

function buildRule(task: TaskRow, firstInstance: TaskInstanceRow) {
  return rrulestr(task.rrule ?? "", {
    dtstart: pickStartDate(firstInstance),
  })
}

function nextOccurrenceAfter(
  rule: ReturnType<typeof rrulestr>,
  afterDate: Date,
): Date | null {
  const nextDate = rule.after(afterDate, false)
  return nextDate ?? null
}

function countFutureInstances(
  instances: TaskInstanceRow[],
  now: Date,
): number {
  return instances.filter((instance) => pickStartDate(instance) > now).length
}

function buildInsertPlans(
  task: TaskRow,
  instances: TaskInstanceRow[],
  now: Date,
): {
  futureInstanceCount: number
  plans: ScheduleInsertPlan[]
} {
  if (instances.length === 0) {
    return {
      futureInstanceCount: 0,
      plans: [],
    }
  }

  const sortedInstances = sortByStartDate(instances)
  const firstInstance = sortedInstances[0]
  const latestInstance = sortedInstances[sortedInstances.length - 1]
  const latestStart = pickStartDate(latestInstance)
  const latestDue = pickDueDate(latestInstance)
  const durationMs = latestDue.getTime() - latestStart.getTime()

  if (durationMs < 0) {
    throw new Error(
      `Task ${task.id} has an instance whose due_date is before start_date`,
    )
  }

  const futureInstanceCount = countFutureInstances(instances, now)
  const insertsNeeded = Math.max(0, FUTURE_INSTANCES_BUFFER - futureInstanceCount)

  if (insertsNeeded === 0) {
    return {
      futureInstanceCount,
      plans: [],
    }
  }

  const rule = buildRule(task, firstInstance)
  const plans: ScheduleInsertPlan[] = []
  const earliestAllowedStart = startOfUtcDay(now)
  let cursor = latestStart < earliestAllowedStart
    ? new Date(earliestAllowedStart.getTime() - 1)
    : latestStart
  let nextInstanceNumber = Math.max(
    ...instances.map((instance) => instance.instance_number),
  ) + 1

  while (plans.length < insertsNeeded) {
    const nextStart = nextOccurrenceAfter(rule, cursor)

    if (!nextStart) {
      break
    }

    // Defensive guard: if the recurrence library ever returns the same instant
    // again, stop instead of looping forever.
    if (sameInstant(nextStart, cursor)) {
      throw new Error(
        `RRULE for task ${task.id} did not advance after ${cursor.toISOString()}`,
      )
    }

    if (nextStart < earliestAllowedStart) {
      cursor = nextStart
      continue
    }

    plans.push({
      instanceNumber: nextInstanceNumber,
      startDate: nextStart,
      dueDate: new Date(nextStart.getTime() + durationMs),
    })

    cursor = nextStart
    nextInstanceNumber += 1
  }

  return {
    futureInstanceCount,
    plans,
  }
}

async function insertInstance(
  supabase: ReturnType<typeof createClient>,
  taskId: string,
  plan: ScheduleInsertPlan,
): Promise<TaskInstanceRow> {
  const payload = {
    task_id: taskId,
    instance_number: plan.instanceNumber,
    start_date: asIsoString(plan.startDate),
    due_date: asIsoString(plan.dueDate),
    original_start_date: asIsoString(plan.startDate),
    original_due_date: asIsoString(plan.dueDate),
  }

  const { data, error } = await supabase
    .from("task_instances")
    .insert(payload)
    .select(
      [
        "id",
        "task_id",
        "parent_instance_id",
        "instance_number",
        "start_date",
        "due_date",
        "original_start_date",
        "original_due_date",
      ].join(", "),
    )
    .single()

  if (error) {
    throw error
  }

  return data as TaskInstanceRow
}

async function fetchChildTasks(
  supabase: ReturnType<typeof createClient>,
  parentTaskId: string,
): Promise<TaskRow[]> {
  const { data, error } = await supabase
    .from("tasks")
    .select("id, title, rrule, is_active")
    .eq("parent_task_id", parentTaskId)

  if (error) {
    throw error
  }

  return (data ?? []) as TaskRow[]
}

function buildChildTemplate(
  childInstances: TaskInstanceRow[],
  anchorParentInstance: TaskInstanceRow,
): ChildTemplate | null {
  const anchorChildInstance = childInstances.find(
    (instance) => instance.parent_instance_id === anchorParentInstance.id,
  )

  if (!anchorChildInstance) {
    return null
  }

  const childStart = pickStartDate(anchorChildInstance)
  const childDue = pickDueDate(anchorChildInstance)
  const parentStart = pickStartDate(anchorParentInstance)

  return {
    firstParentInstanceNumber: anchorParentInstance.instance_number,
    offsetMs: childStart.getTime() - parentStart.getTime(),
    durationMs: childDue.getTime() - childStart.getTime(),
  }
}

function buildMissingChildPlans(
  childInstances: TaskInstanceRow[],
  childTemplate: ChildTemplate,
  parentInstances: TaskInstanceRow[],
): ChildSpawnPlan[] {
  const existingParentIds = new Set(
    childInstances
      .map((instance) => instance.parent_instance_id)
      .filter((value): value is string => Boolean(value)),
  )

  const plans: ChildSpawnPlan[] = []

  for (const parentInstance of sortByStartDate(parentInstances)) {
    if (
      parentInstance.instance_number
      <= childTemplate.firstParentInstanceNumber
    ) {
      continue
    }

    if (existingParentIds.has(parentInstance.id)) {
      continue
    }

    const parentStart = pickStartDate(parentInstance)
    const parentDue = pickDueDate(parentInstance)
    const childStart = new Date(
      parentStart.getTime() + childTemplate.offsetMs,
    )
    const childDue = new Date(
      childStart.getTime() + childTemplate.durationMs,
    )

    if (childStart < parentStart || childDue > parentDue) {
      throw new Error(
        `Child instance ${parentInstance.instance_number} would fall `
        + `outside parent window for parent instance ${parentInstance.id}`,
      )
    }

    plans.push({
      parentInstanceId: parentInstance.id,
      instanceNumber: parentInstance.instance_number,
      startDate: childStart,
      dueDate: childDue,
    })
  }

  return plans
}

async function insertChildInstance(
  supabase: ReturnType<typeof createClient>,
  childTaskId: string,
  plan: ChildSpawnPlan,
): Promise<void> {
  const payload = {
    task_id: childTaskId,
    parent_instance_id: plan.parentInstanceId,
    instance_number: plan.instanceNumber,
    start_date: asIsoString(plan.startDate),
    due_date: asIsoString(plan.dueDate),
    original_start_date: asIsoString(plan.startDate),
    original_due_date: asIsoString(plan.dueDate),
  }

  const { error } = await supabase.from("task_instances").insert(payload)
  if (error) {
    throw error
  }
}

async function catchUpChildTasks(
  supabase: ReturnType<typeof createClient>,
  parentTaskId: string,
  anchorParentInstance: TaskInstanceRow,
  parentInstances: TaskInstanceRow[],
  dryRun: boolean,
): Promise<Array<{ childTaskId: string; created: number }>> {
  const childTasks = await fetchChildTasks(supabase, parentTaskId)
  if (childTasks.length === 0) {
    return []
  }

  const results: Array<{ childTaskId: string; created: number }> = []

  for (const childTask of childTasks) {
    const childInstances = await fetchTaskInstances(supabase, childTask.id)
    const template = buildChildTemplate(childInstances, anchorParentInstance)

    if (!template) {
      results.push({ childTaskId: childTask.id, created: 0 })
      continue
    }

    const plans = buildMissingChildPlans(
      childInstances,
      template,
      parentInstances,
    )

    if (!dryRun) {
      for (const plan of plans) {
        await insertChildInstance(supabase, childTask.id, plan)
      }
    }

    results.push({ childTaskId: childTask.id, created: plans.length })
  }

  return results
}

serve(async (req) => {
  if (!isAuthorizedSchedulerRequest(req)) {
    return UNAUTHORIZED_RESPONSE
  }

  let rawBody: unknown = null
  try {
    rawBody = await req.json()
  } catch {
    rawBody = null
  }

  const { dry_run } = parseRequestBody(rawBody)

  const supabase = createClient(
    Deno.env.get("SB_API_URL") ?? "",
    Deno.env.get("ADHD_COMPANION_KEY") ?? "",
  )

  const {
    data: statusAgingResults,
    error: statusAgingError,
  } = await supabase.rpc("age_task_instance_statuses", {
    p_debt_days: DAYS_OVERDUE_BEFORE_DEBT,
    p_stale_days: DAYS_IN_DEBT_BEFORE_STALE,
    p_dry_run: dry_run,
  })

  if (statusAgingError) {
    return new Response(
      JSON.stringify(
        {
          message: "Could not age task instance statuses",
          error: statusAgingError.message,
        },
        null,
        2,
      ),
      {
        status: 500,
        headers: { "Content-Type": "application/json" },
      },
    )
  }

  const { data: tasks, error: tasksError } = await supabase
    .from("tasks")
    .select("id, title, rrule, is_active")
    .eq("is_active", true)
    .not("rrule", "is", null)

  if (tasksError) {
    return new Response(
      JSON.stringify(
        {
          message: "Could not load recurring tasks",
          error: tasksError.message,
        },
        null,
        2,
      ),
      {
        status: 500,
        headers: { "Content-Type": "application/json" },
      },
    )
  }

  const now = new Date()
  const results = []

  for (const task of (tasks ?? []) as TaskRow[]) {
    try {
      const instances = await fetchTaskInstances(supabase, task.id)

      if (instances.length === 0) {
        results.push({
          taskId: task.id,
          title: task.title,
          createdInstances: 0,
          skipped: "Task has no base instance to anchor the RRULE",
        })
        continue
      }

      const { futureInstanceCount, plans } = buildInsertPlans(task, instances, now)

      const createdInstances: TaskInstanceRow[] = []
      if (!dry_run) {
        for (const plan of plans) {
          const inserted = await insertInstance(supabase, task.id, plan)
          createdInstances.push(inserted)
        }
      }

      const anchorParentInstance = sortByStartDate(instances)[instances.length - 1]
      const parentInstancesForChildren = dry_run
        ? [
            ...instances,
            ...plans.map((plan) => ({
              id: `dry-run-parent-${task.id}-${plan.instanceNumber}`,
              task_id: task.id,
              parent_instance_id: null,
              instance_number: plan.instanceNumber,
              start_date: asIsoString(plan.startDate),
              due_date: asIsoString(plan.dueDate),
              original_start_date: asIsoString(plan.startDate),
              original_due_date: asIsoString(plan.dueDate),
            })),
          ]
        : [...instances, ...createdInstances]

      const childResults = await catchUpChildTasks(
        supabase,
        task.id,
        anchorParentInstance,
        parentInstancesForChildren,
        dry_run,
      )

      results.push({
        taskId: task.id,
        title: task.title,
        futureInstanceCount,
        targetFutureBuffer: FUTURE_INSTANCES_BUFFER,
        createdInstances: dry_run ? plans.length : createdInstances.length,
        createdChildInstances: childResults,
        instancePlans: plans.map((plan) => ({
          instanceNumber: plan.instanceNumber,
          startDate: asIsoString(plan.startDate),
          dueDate: asIsoString(plan.dueDate),
        })),
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      results.push({
        taskId: task.id,
        title: task.title,
        error: message,
      })
    }
  }

  return new Response(
    JSON.stringify(
      {
        message: dry_run ? "Cron executed in dry-run mode" : "Cron executed",
        dry_run,
        futureInstancesBuffer: FUTURE_INSTANCES_BUFFER,
        daysOverdueBeforeDebt: DAYS_OVERDUE_BEFORE_DEBT,
        daysInDebtBeforeStale: DAYS_IN_DEBT_BEFORE_STALE,
        statusAging: statusAgingResults,
        results,
      },
      null,
      2,
    ),
    {
      headers: { "Content-Type": "application/json" },
    },
  )
})
