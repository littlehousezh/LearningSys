let currentSessionId = null
let currentSession = null
let hubTasks = []
let pendingOperation = null
let pendingTimerId = null
let autoRunInFlight = false
let draftStateByRole = {}
let completionRecordedBySessionId = {}
let practiceCompletionStore = loadPracticeCompletionStore()
let practiceCompletion = {}
let liveCompletionFlash = null
let fileViewerCache = {}

const HUB_TASKS_CONFIG = [
  { slug: 'planner', role: 'Task Planner', cardId: 'task-card-1', buttonId: 'hub-start-btn-1', startLabel: 'Start Planning Practice' },
  { slug: 'coder', role: 'Patch Author', cardId: 'task-card-2', buttonId: 'hub-start-btn-2', startLabel: 'Start Coding Practice' },
  { slug: 'reviewer', role: 'Code Reviewer', cardId: 'task-card-3', buttonId: 'hub-start-btn-3', startLabel: 'Start Review Practice' },
  { slug: 'tester', role: 'Test Runner', cardId: 'task-card-4', buttonId: 'hub-start-btn-4', startLabel: 'Start Testing Practice' }
]

function workflowRoleReference (role) {
  const references = {
    'Task Planner': {
      shortLabel: 'Turns the issue into a scoped implementation plan.',
      receives: ['Issue statement', 'Task hints', 'Likely repo files'],
      produces: ['Plan summary', 'Root-cause hypothesis', 'Likely code areas', 'Acceptance checks'],
      transitionNotes: ['Then the workflow moves to Patch Author.'],
      handoffPurpose: 'The plan gives the Patch Author a focused place to start.'
    },
    'Patch Author': {
      shortLabel: 'Proposes the smallest concrete code change.',
      receives: ['Planner handoff', 'Issue statement', 'Relevant files'],
      produces: ['Patch status', 'Files changed', 'Implementation plan', 'Proposed diff', 'Done criteria'],
      transitionNotes: ['Then the workflow moves to Code Reviewer.'],
      handoffPurpose: 'The reviewer needs a concrete proposal before judging correctness.'
    },
    'Code Reviewer': {
      shortLabel: 'Checks whether the proposed fix is coherent and safe to test.',
      receives: ['Patch proposal', 'Proposed diff', 'Files changed', 'Issue context'],
      produces: ['Review decision', 'Review notes'],
      transitionNotes: [
        'APPROVE sends the workflow to Test Runner.',
        'REVISE sends the workflow back to Patch Author.'
      ],
      handoffPurpose: 'The review either authorizes testing or explains what must change first.'
    },
    'Test Runner': {
      shortLabel: 'Judges whether the available evidence is strong enough to finish.',
      receives: ['Planner checks', 'Patch proposal', 'Review decision', 'Recorded test output if available'],
      produces: ['Test decision', 'Test notes'],
      transitionNotes: [
        'PASS completes the workflow.',
        'FAIL returns the workflow to Patch Author when the evidence shows a real defect.'
      ],
      handoffPurpose: 'Testing closes the loop by deciding whether the patch is proven enough to ship.'
    }
  }

  return references[role] || {
    shortLabel: 'Contributes to the current workflow step.',
    receives: [],
    produces: [],
    transitionNotes: [],
    handoffPurpose: ''
  }
}

function referenceLinkMeta (task) {
  const url = String(task?.issue_url || task?.benchmark_url || '').trim()
  const benchmarkUrl = String(task?.benchmark_url || '').trim()

  if (!url) return { label: 'Reference', url: '' }
  if (url === benchmarkUrl || url.includes('soarsmu/BugsInPy')) {
    return { label: 'Benchmark', url }
  }
  if (url.includes('/issues/') && !url.includes('/issues?q=')) {
    return { label: 'Upstream issue', url }
  }
  if (url.includes('/issues?q=')) {
    return { label: 'Upstream reference', url }
  }
  return { label: 'Reference', url }
}

function sleep (ms) {
  return new Promise(resolve => window.setTimeout(resolve, ms))
}

function loadPracticeCompletionStore () {
  try {
    window.localStorage.removeItem('workflow-practice-completion')
  } catch (err) {
  }
  return {}
}

function participantCompletionKey (participantId) {
  return String(participantId || '').trim()
}

function activeParticipantId () {
  const input = document.getElementById('hub-participant-name')
  const typed = (input?.value || '').trim()
  return typed || currentSession?.participant_id || currentSession?.participant_name || ''
}

function syncPracticeCompletion (participantId = activeParticipantId()) {
  const key = participantCompletionKey(participantId)
  const scoped = key ? practiceCompletionStore[key] : null
  practiceCompletion = scoped && typeof scoped === 'object' ? { ...scoped } : {}
}

function savePracticeCompletion (participantId = activeParticipantId()) {
  const key = participantCompletionKey(participantId)
  if (!key) return
  if (Object.keys(practiceCompletion).length) {
    practiceCompletionStore = { ...practiceCompletionStore, [key]: practiceCompletion }
  } else {
    const nextStore = { ...practiceCompletionStore }
    delete nextStore[key]
    practiceCompletionStore = nextStore
  }
}

function completedPracticeCount () {
  return HUB_TASKS_CONFIG.filter(task => Boolean(practiceCompletion[task.slug])).length
}

function nextPendingPracticeTask () {
  return HUB_TASKS_CONFIG.find(task => !practiceCompletion[task.slug]) || null
}

function markPracticeCompleted (slug) {
  if (!slug || practiceCompletion[slug]) return false
  practiceCompletion = {
    ...practiceCompletion,
    [slug]: {
      completedAt: new Date().toISOString()
    }
  }
  savePracticeCompletion()
  return true
}

function buildHubProgressMessage () {
  const completed = completedPracticeCount()
  const nextTask = nextPendingPracticeTask()
  if (!completed) {
    return 'Pick a BugsInPy practice task to begin. Completed tasks will dim after you finish them.'
  }
  if (!nextTask) {
    return 'All four curated BugsInPy practice tasks are complete.'
  }
  return `Completed ${completed} of ${HUB_TASKS_CONFIG.length} practice tasks. Next recommended task: ${nextTask.role}.`
}

function showStudyHub () {
  document.getElementById('study-hub-view').style.display = ''
  document.getElementById('launch-view').style.display = 'none'
}

function showLaunchView () {
  document.getElementById('study-hub-view').style.display = 'none'
  document.getElementById('launch-view').style.display = ''
}

async function api (url, options = {}) {
  const participantId = activeParticipantId()
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) }
  if (participantId) headers['X-Participant-ID'] = participantId
  const res = await fetch(url, { ...options, headers })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
  return data
}

function setText (id, text) {
  const el = document.getElementById(id)
  if (el) el.textContent = text || ''
}

function escHtml (value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

function fmtTime (iso) {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
}

function fmtDuration (seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return ''
  const value = Number(seconds)
  if (value < 60) return `${Math.round(value)}s`
  return `${Math.floor(value / 60)}m ${String(Math.round(value % 60)).padStart(2, '0')}s`
}

function clipText (value, max = 2400) {
  const text = String(value || '').trim()
  if (!text) return ''
  return text.length > max ? `${text.slice(0, max)}\n...` : text
}

function looksLikeCodeText (value) {
  const text = String(value || '')
  if (!text.trim()) return false
  return (
    /^diff --git/m.test(text) ||
    /^@@/m.test(text) ||
    /^(---|\+\+\+)/m.test(text) ||
    /^\s*(def|class|import|from)\s+/m.test(text) ||
    /[{}();]/.test(text) ||
    /```/.test(text)
  )
}

function artifactNeedsCodeBlock (artifact) {
  const kind = String(artifact?.kind || '').toLowerCase()
  if (['patch', 'code', 'file', 'test_run', 'prompt'].includes(kind)) return true
  return looksLikeCodeText(artifact?.content || '')
}

function roleToneClass (role) {
  return {
    'Task Planner': 'role-tone-planner',
    'Patch Author': 'role-tone-coder',
    'Code Reviewer': 'role-tone-reviewer',
    'Test Runner': 'role-tone-tester'
  }[role] || ''
}

function contentLineCount (value) {
  const text = String(value || '')
  if (!text) return 0
  return text.split(/\r?\n/).length
}

function shouldCollapseArtifact (artifact) {
  const kind = String(artifact?.kind || '').toLowerCase()
  if (kind === 'prompt') return true
  if (['patch', 'code', 'file', 'test_run'].includes(kind)) return true
  return artifactNeedsCodeBlock(artifact)
}

function showHubStatus (message, isError = false) {
  const box = document.getElementById('hub-status-box')
  if (!box) return
  box.textContent = message
  box.style.display = message ? '' : 'none'
  box.classList.toggle('state-error', isError)
}

function clearInlineNotices () {
  const wrap = document.getElementById('inline-notices')
  if (wrap) wrap.innerHTML = ''
}

function avatarLabel (actor, role) {
  if (actor === 'human') return 'YOU'
  const labels = {
    'Task Planner': 'PLAN',
    'Patch Author': 'CODE',
    'Code Reviewer': 'REVIEW',
    'Test Runner': 'TEST'
  }
  return labels[role] || 'AI'
}

function draftKeyForSession (session) {
  return session?.manual_step_slug || session?.manual_step_role || 'manual-step'
}

function manualStepDefinition (session) {
  return (session?.manual_step_options || []).find(option => option.slug === session.manual_step_slug) || null
}

function inputStepDefinition (session) {
  if (!session) return null
  const currentStep = session.current_step || null
  if (
    session.status === 'waiting_for_human' &&
    currentStep &&
    currentStep.role === (session.waiting_role || session.manual_step_role)
  ) {
    return currentStep
  }
  return manualStepDefinition(session) || currentStep
}

function inputTemplateForSession (session) {
  const step = inputStepDefinition(session)
  return step?.human_prompt_template || session?.step_briefing?.response_format || ''
}

function inputRoleForSession (session) {
  return inputStepDefinition(session)?.role || session?.manual_step_role || 'Current role'
}

function syncInputDraft (session, options = {}) {
  const textarea = document.getElementById('task-input')
  if (!textarea || !session) return

  const key = draftKeyForSession(session)
  const template = inputTemplateForSession(session)
  const existing = draftStateByRole[key]

  if (!existing || options.forceTemplate || existing.template !== template) {
    draftStateByRole[key] = {
      template,
      text: options.forceTemplate
        ? template
        : (existing?.touched ? existing.text : (existing?.text || template)),
      touched: options.forceTemplate ? false : Boolean(existing?.touched && existing.text !== template)
    }
  }

  const state = draftStateByRole[key]
  if (!state.text) {
    state.text = template
    state.touched = false
  }

  textarea.dataset.draftKey = key
  if (textarea.value !== state.text) textarea.value = state.text
}

function cacheCurrentDraft () {
  const textarea = document.getElementById('task-input')
  if (!textarea || !currentSession) return
  const key = textarea.dataset.draftKey || draftKeyForSession(currentSession)
  const template = inputTemplateForSession(currentSession)
  const text = textarea.value || ''
  draftStateByRole[key] = {
    template,
    text,
    touched: text !== template
  }
}

function setDraftText (session, text) {
  const textarea = document.getElementById('task-input')
  const key = draftKeyForSession(session)
  const template = inputTemplateForSession(session)
  draftStateByRole[key] = {
    template,
    text,
    touched: text !== template
  }
  if (textarea) {
    textarea.dataset.draftKey = key
    textarea.value = text
  }
}

function resetDraftForSession (session) {
  const key = draftKeyForSession(session)
  const template = inputTemplateForSession(session)
  draftStateByRole[key] = {
    template,
    text: template,
    touched: false
  }
}

function shouldAutoAdvance (session) {
  return Boolean(
    currentSessionId &&
    session &&
    session.status !== 'waiting_for_human' &&
    session.status !== 'completed'
  )
}

function recordPracticeCompletionIfNeeded (session) {
  if (!session || session.status !== 'completed' || !currentSessionId) return
  if (completionRecordedBySessionId[currentSessionId]) return
  completionRecordedBySessionId[currentSessionId] = true
  syncPracticeCompletion(session.participant_id || session.participant_name)
  markPracticeCompleted(session.manual_step_slug)
  renderHubTaskProgress()
}

function completionNextActionMessage () {
  const nextTask = nextPendingPracticeTask()
  if (!nextTask) {
    return 'This practice task is complete. Return to Study Hub to review the finished practice set.'
  }
  return `This practice task is complete. Return to Study Hub to start ${nextTask.role} Practice.`
}

function renderHubTaskCards () {
  HUB_TASKS_CONFIG.forEach((config, idx) => {
    const task = hubTasks[idx]
    const card = document.getElementById(config.cardId)
    if (!card || !task) return
    const roleEl = card.querySelector('.task-role')
    const rationaleEl = card.querySelector('.task-rationale')
    if (roleEl) {
      roleEl.innerHTML = `You replace the <strong>${escHtml(config.role)}</strong> agent on <strong>${escHtml(task.instance_id)}</strong>.`
    }
    if (rationaleEl) {
      rationaleEl.textContent = `${task.task_focus || task.problem_statement} ${task.educational_fit?.selection_reason || ''}`.trim()
    }
  })
}

function renderHubTaskProgress () {
  const nextTask = nextPendingPracticeTask()

  HUB_TASKS_CONFIG.forEach(task => {
    const card = document.getElementById(task.cardId)
    const button = document.getElementById(task.buttonId)
    if (!card || !button) return

    const statusEl = card.querySelector('.task-status')
    const completed = Boolean(practiceCompletion[task.slug])
    const isNext = !completed && nextTask?.slug === task.slug

    card.classList.toggle('task-completed', completed)
    card.classList.toggle('task-next', isNext)

    if (statusEl) {
      statusEl.classList.remove('status-not-started', 'status-next', 'status-completed')
      statusEl.classList.add(completed ? 'status-completed' : (isNext ? 'status-next' : 'status-not-started'))
      statusEl.textContent = completed ? 'Completed' : (isNext ? 'Next Up' : 'Pending')
    }

    button.classList.toggle('completed', completed)
    button.disabled = completed
    button.textContent = completed
      ? 'Completed'
      : (isNext ? `Next: ${task.startLabel}` : task.startLabel)
  })

  showHubStatus(buildHubProgressMessage())
}

async function previewEasiest () {
  try {
    const data = await api('/api/tasks/easiest')
    hubTasks = data.tasks || []
    for (let i = 0; i < 4; i += 1) {
      const task = hubTasks[i] || null
      setText(`hub-task-id-${i + 1}`, task ? task.instance_id : 'Unavailable')
      setText(`hub-task-focus-${i + 1}`, task ? (task.task_focus || task.repo) : 'Unavailable')
      setText(`hub-task-fit-${i + 1}`, task ? (task.educational_fit?.difficulty_label || task.educational_fit?.difficulty_band || '') : '')
    }
    renderHubTaskCards()
    renderHubTaskProgress()
  } catch (err) {
    showHubStatus(String(err), true)
  }
}

async function openTask (manualStep, taskIndex) {
  const participant = activeParticipantId()
  if (!participant) {
    alert('Enter your participant ID first.')
    return
  }

  syncPracticeCompletion(participant)

  const btn = document.getElementById(`hub-start-btn-${taskIndex}`)
  if (btn) btn.disabled = true
  showHubStatus('Creating a BugsInPy practice session. Repo checkout may take a moment.')

  try {
    if (!hubTasks.length) await previewEasiest()
    const task = hubTasks[taskIndex - 1] || null
    const payload = {
      participant_id: participant,
      participant_name: participant,
      workflow_type: manualStep,
      manual_step: manualStep,
      instance_id: task?.instance_id
    }
    const data = await api('/api/sessions', {
      method: 'POST',
      body: JSON.stringify(payload)
    })
    currentSessionId = data.session.session_id
    currentSession = null
    liveCompletionFlash = null
    fileViewerCache = {}
    clearInlineNotices()
    closeAgentDetail()
    closeFileViewer()
    showHubStatus('')
    showLaunchView()
    renderSession(data.session)
    await autoAdvanceWorkflow(data.session)
  } catch (err) {
    showHubStatus(String(err), true)
  } finally {
    if (btn) btn.disabled = false
  }
}

function renderParticipantBanner (session) {
  const task = session.task || {}
  const summary = buildIssueSummary(task)
  setText('banner-title', `${session.manual_step_role} Practice`)
  setText('banner-issue-summary', `Issue summary: ${summary}`)

  const repoA = document.getElementById('banner-repo-link')
  const issueA = document.getElementById('banner-issue-link-launch')
  const issueMeta = referenceLinkMeta(task)
  const issueUrl = String(issueMeta.url || '').trim()
  const showReference = issueUrl.includes('github.com')

  if (repoA) {
    repoA.href = task.repo_url || '#'
    repoA.style.display = task.repo_url ? '' : 'none'
  }
  if (issueA) {
    issueA.href = showReference ? issueUrl : '#'
    issueA.textContent = issueMeta.label || 'Reference'
    issueA.style.display = showReference ? '' : 'none'
  }
}

function buildIssueSummary (task) {
  const explicit = String(task?.issue_summary || '').trim()
  if (explicit) return explicit

  const raw = String(task?.problem_statement || task?.task_focus || 'Review the assigned issue and decide what should change.')
    .replace(/`/g, '')
    .replace(/\s+/g, ' ')
    .trim()

  if (!raw) return 'Review the assigned issue and decide what should change.'

  const firstSentenceMatch = raw.match(/^.*?[.!?](?:\s|$)/)
  const sentence = (firstSentenceMatch ? firstSentenceMatch[0] : raw).trim()
  if (sentence.length <= 180) return sentence
  return `${sentence.slice(0, 177).trimEnd()}...`
}

function buildPendingProgressText () {
  if (!pendingOperation?.active) return 'The system will show active workflow progress here.'
  const elapsedSeconds = Math.max(0, Math.floor((Date.now() - pendingOperation.startedAt) / 1000))
  return `${pendingOperation.detail} Elapsed: ${elapsedSeconds}s`
}

function animatedPendingLabel (label) {
  const dots = '.'.repeat((Math.floor(Date.now() / 500) % 3) + 1)
  return `${label}${dots}`
}

function renderStatusDisplay (session) {
  const status = pendingOperation?.systemStatus || session.system_status || {}
  const el = document.getElementById('status-display')
  const labelEl = document.getElementById('status-label-text')
  if (!el || !labelEl) return

  labelEl.textContent = pendingOperation?.active
    ? animatedPendingLabel(status.label || session.status || 'Working')
    : (status.label || session.status || 'Idle')

  el.classList.toggle('status-active', pendingOperation?.active || session.status === 'running' || session.status === 'waiting_for_human')
  el.classList.toggle('status-busy', Boolean(pendingOperation?.active))

  const spinnerEl = document.getElementById('status-spinner')
  if (spinnerEl) spinnerEl.classList.toggle('visible', Boolean(pendingOperation?.active))

  const progressEl = document.getElementById('progress-status')
  if (!progressEl) return
  if (pendingOperation?.active) {
    progressEl.textContent = buildPendingProgressText()
    progressEl.classList.add('progress-active')
    progressEl.classList.remove('progress-complete')
  } else if (session.status === 'completed') {
    progressEl.textContent = completionNextActionMessage()
    progressEl.classList.remove('progress-active')
    progressEl.classList.add('progress-complete')
  } else {
    progressEl.textContent = status.detail || 'The system will show active workflow progress here.'
    progressEl.classList.remove('progress-active')
    progressEl.classList.remove('progress-complete')
  }
}

function renderSequence(sequence) {
  const wrap = document.createElement('div')
  wrap.className = 'workflow-sequence'
  ;(sequence || []).forEach(item => {
    const block = document.createElement('div')
    block.className = 'workflow-sequence-step'
    block.innerHTML = `
      <div class="workflow-sequence-label">${escHtml(item.label || '')}</div>
      <div class="workflow-sequence-detail">${escHtml(item.detail || '')}</div>
    `
    wrap.appendChild(block)
  })
  return wrap
}

function latestRunForAgent (agent, process) {
  if (agent.status === 'current' && process?.current_activity?.role === agent.role) {
    return process.current_activity
  }
  if (agent.runs?.length) return agent.runs[agent.runs.length - 1]
  return null
}

function shortPathLabel (value) {
  if (!value) return ''
  const text = String(value)
  const parts = text.split('/')
  return parts.length > 3 ? parts.slice(-3).join('/') : text
}

function uniqueItems (items, limit = 4) {
  return [...new Set((items || []).filter(Boolean))].slice(0, limit)
}

function textFocusItems (items, limit = 4) {
  return uniqueItems(items, limit).map(label => ({ kind: 'text', label }))
}

function liveFiles (activity) {
  const files = activity?.files_in_scope || []
  const seen = new Set()
  const output = []
  files.forEach(file => {
    const path = (file?.path || '').trim()
    if (!path || seen.has(path) || output.length >= 4) return
    seen.add(path)
    output.push({
      kind: 'file',
      label: file.label || shortPathLabel(path),
      path
    })
  })
  return output
}

function liveArtifacts (artifacts) {
  return textFocusItems((artifacts || []).map(artifact => artifact.title || artifact.kind || artifact.origin))
}

function stageSummaryItems (activity, mode) {
  if (mode === 'files') return liveFiles(activity)
  if (mode === 'inputs') return liveArtifacts(activity?.input_artifacts)
  if (mode === 'outputs') return liveArtifacts(activity?.output_artifacts)
  if (mode === 'handoff') {
    return textFocusItems([
      activity?.handoff?.to_role ? `To ${activity.handoff.to_role}` : '',
      activity?.handoff?.prompt_artifact?.title || activity?.handoff?.prompt_artifact?.kind || 'Handoff packet'
    ])
  }
  return textFocusItems([
    activity?.prompt_artifact?.title || activity?.prompt_artifact?.kind || '',
    ...liveFiles(activity).map(item => item.label),
    ...liveArtifacts(activity?.input_artifacts).map(item => item.label)
  ])
}

function stageTemplatesForRole (role) {
  const templates = {
    'Task Planner': [
      {
        label: 'Interpreting the issue',
        detail: 'Reading the bug report to decide what the problem is really asking for.',
        focusLabel: 'Input context',
        focusMode: 'inputs'
      },
      {
        label: 'Mapping likely files',
        detail: 'Narrowing the code area that is most likely involved in the bug.',
        focusLabel: 'Files in focus',
        focusMode: 'files'
      },
      {
        label: 'Drafting the plan',
        detail: 'Turning the issue into a concrete implementation path and success checks.',
        focusLabel: 'Planning from',
        focusMode: 'prompt'
      },
      {
        label: 'Preparing handoff',
        detail: 'Packaging the plan so the Patch Author can act on it next.',
        focusLabel: 'Passing forward',
        focusMode: 'handoff'
      }
    ],
    'Patch Author': [
      {
        label: 'Reading the plan and handoff',
        detail: 'Using the planning output and current context to understand exactly what needs to change.',
        focusLabel: 'Working from',
        focusMode: 'inputs'
      },
      {
        label: 'Inspecting the target code',
        detail: 'Narrowing the relevant files and functions so the proposed fix stays small and specific.',
        focusLabel: 'Files in focus',
        focusMode: 'files'
      },
      {
        label: 'Drafting the proposed patch',
        detail: 'Writing the structured code-change proposal or diff that the reviewer will inspect next.',
        focusLabel: 'Producing',
        focusMode: 'prompt'
      },
      {
        label: 'Preparing handoff',
        detail: 'Summarizing the proposed change so the reviewer can check it next.',
        focusLabel: 'Passing forward',
        focusMode: 'handoff'
      }
    ],
    'Code Reviewer': [
      {
        label: 'Loading the proposal',
        detail: 'Reading the plan and candidate fix before making a judgment.',
        focusLabel: 'Inputs in focus',
        focusMode: 'inputs'
      },
      {
        label: 'Checking correctness',
        detail: 'Looking for behavioral risks, missing cases, or weak assumptions in the actual proposal and handoff.',
        focusLabel: 'Inputs in focus',
        focusMode: 'inputs'
      },
      {
        label: 'Forming the review decision',
        detail: 'Deciding whether the fix is ready to advance or should loop back because a concrete problem still blocks testing.',
        focusLabel: 'Producing',
        focusMode: 'outputs'
      },
      {
        label: 'Preparing handoff',
        detail: 'Sending approval notes or revision guidance to the next stage.',
        focusLabel: 'Passing forward',
        focusMode: 'handoff'
      }
    ],
    'Test Runner': [
      {
        label: 'Gathering evidence',
        detail: 'Collecting the signals that matter for judging whether the bug is resolved.',
        focusLabel: 'Inputs in focus',
        focusMode: 'inputs'
      },
      {
        label: 'Checking expected behavior',
        detail: 'Comparing the proposed fix, review notes, and validation targets against the behavior the system should show.',
        focusLabel: 'Validation evidence',
        focusMode: 'inputs'
      },
      {
        label: 'Forming the validation decision',
        detail: 'Deciding whether there is enough evidence to finish or whether the current evidence shows a real defect that needs another iteration.',
        focusLabel: 'Producing',
        focusMode: 'outputs'
      },
      {
        label: 'Preparing handoff',
        detail: 'Writing the validation outcome so the workflow can finish or continue cleanly.',
        focusLabel: 'Passing forward',
        focusMode: 'handoff'
      }
    ]
  }
  return templates[role] || []
}

function liveStagesForSession (session, agent, activity) {
  if (session.status === 'waiting_for_human' && session.waiting_role === agent.role) {
    return [{
      label: 'Waiting for your response',
      detail: session.step_briefing?.what_you_should_do || 'The workflow is paused for your response.',
      focusLabel: 'Use this prompt',
      items: textFocusItems([
        activity?.prompt_artifact?.title,
        session.step_briefing?.response_format ? 'Response scaffold' : ''
      ])
    }]
  }

  if (pendingOperation?.active) {
    return stageTemplatesForRole(agent.role).map(template => ({
      ...template,
      items: stageSummaryItems(activity, template.focusMode)
    }))
  }

  if (session.status === 'completed') {
    return [{
      label: 'Workflow complete',
      detail: 'The live workflow is finished. Click a role on the right to inspect how each agent worked.',
      focusLabel: 'Review next',
      items: textFocusItems(['Open any role to inspect its full workflow'])
    }]
  }

  return [{
    label: 'Preparing the next move',
    detail: activity?.goal || agent.ai_instruction,
    focusLabel: 'Current context',
    items: stageSummaryItems(activity, 'prompt')
  }]
}

function currentLiveStage (session, agent, activity) {
  const stages = liveStagesForSession(session, agent, activity)
  if (!stages.length) return null

  if (!pendingOperation?.active) return stages[0]

  const elapsed = Date.now() - pendingOperation.startedAt
  const stepWindowMs = 1800
  const index = Math.min(stages.length - 1, Math.floor(elapsed / stepWindowMs))
  return stages[index]
}

function buildCompletionFlash (session, role) {
  if (!session || !role) return null
  const agents = session.workflow_process?.agents || []
  const agent = agents.find(item => item.role === role)
  const run = agent?.runs?.length ? agent.runs[agent.runs.length - 1] : null
  if (!agent || !run) return null

  return {
    role: agent.role,
    mode: agent.mode,
    steps: uniqueItems((run.sequence || []).map(step => step.label), 5),
    summary: (run.output_artifacts || []).map(artifact => artifact.title).filter(Boolean)[0] || run.handoff?.prompt_artifact?.title || 'Contribution recorded'
  }
}

function renderWorkflowEducationCard (agent) {
  const reference = workflowRoleReference(agent.role)
  const card = document.createElement('div')
  card.className = 'live-education-card'
  card.innerHTML = '<div class="artifact-group-label">How this step fits</div>'

  const grid = document.createElement('div')
  grid.className = 'live-education-grid'

  ;[
    { label: 'Receives', items: reference.receives },
    { label: 'Produces', items: reference.produces },
    { label: 'Moves next when', items: reference.transitionNotes }
  ].forEach(section => {
    const block = document.createElement('div')
    block.className = 'live-education-section'
    block.innerHTML = `<div class="live-education-label">${escHtml(section.label)}</div>`
    const items = document.createElement('div')
    items.className = 'live-education-items'
    ;(section.items || []).forEach(item => {
      const chip = document.createElement('div')
      chip.className = 'live-focus-chip'
      chip.textContent = item
      items.appendChild(chip)
    })
    if (!items.childElementCount) {
      const empty = document.createElement('div')
      empty.className = 'artifact-empty'
      empty.textContent = 'No teaching note recorded.'
      items.appendChild(empty)
    }
    block.appendChild(items)
    grid.appendChild(block)
  })

  card.appendChild(grid)

  if (reference.handoffPurpose) {
    const note = document.createElement('div')
    note.className = 'live-education-note'
    note.textContent = reference.handoffPurpose
    card.appendChild(note)
  }

  return card
}

function renderWorkflowBoard (session) {
  const board = document.getElementById('workflow-board')
  const boardStatus = document.getElementById('workflow-board-status')
  if (!board || !boardStatus) return

  const process = session.workflow_process || {}
  const agents = process.agents || []
  board.innerHTML = ''

  if (!agents.length) {
    board.innerHTML = '<div class="no-active-nodes">No workflow loaded</div>'
    boardStatus.textContent = 'No workflow loaded'
    return
  }

  if (liveCompletionFlash) {
    boardStatus.textContent = `Completed: ${liveCompletionFlash.role}`
    const card = document.createElement('section')
    card.className = `workflow-agent-card status-completed mode-${liveCompletionFlash.mode || 'auto'} completion-flash`
    card.innerHTML = `
      <div class="workflow-agent-head">
        <div>
          <div class="workflow-agent-role">${escHtml(liveCompletionFlash.role)}</div>
          <div class="workflow-agent-meta">This agent just finished its contribution.</div>
        </div>
        <div class="workflow-agent-badges">
          <span class="workflow-badge">completed</span>
        </div>
      </div>
    `

    const flash = document.createElement('div')
    flash.className = 'completion-flash-card'
    flash.innerHTML = `
      <div class="live-step-label">Contribution Summary</div>
      <div class="live-step-detail">${escHtml(liveCompletionFlash.summary)}</div>
    `
    card.appendChild(flash)

    const steps = document.createElement('div')
    steps.className = 'completion-step-list'
    ;(liveCompletionFlash.steps || []).forEach(step => {
      const item = document.createElement('div')
      item.className = 'completion-step-item'
      item.textContent = step
      steps.appendChild(item)
    })
    card.appendChild(steps)
    board.appendChild(card)
    return
  }

  const currentRole = process.current_activity?.role || session.system_status?.current_role
  const currentAgent = agents.find(agent => agent.role === currentRole) || agents.find(agent => agent.status === 'current') || agents[agents.length - 1]
  const activity = latestRunForAgent(currentAgent, process) || process.current_activity
  boardStatus.textContent = pendingOperation?.active
    ? `Active now: ${currentAgent.role}`
    : (session.status === 'completed' ? 'Workflow complete' : `Current focus: ${currentAgent.role}`)

  const focus = currentLiveStage(session, currentAgent, activity)
  const card = document.createElement('section')
  card.className = `workflow-agent-card ${roleToneClass(currentAgent.role)} status-${currentAgent.status} mode-${currentAgent.mode}`.trim()
  if (pendingOperation?.active && currentRole === currentAgent.role) card.classList.add('busy')

  card.innerHTML = `
    <div class="workflow-agent-head">
      <div>
        <div class="workflow-agent-role">${escHtml(currentAgent.role)}</div>
        <div class="workflow-agent-meta">${escHtml(currentAgent.mode === 'manual' ? 'Current role: you' : 'Current role: system')}</div>
      </div>
      <div class="workflow-agent-badges">
        <span class="workflow-badge">${escHtml(currentAgent.status)}</span>
      </div>
    </div>
  `

  const liveStep = document.createElement('div')
  liveStep.className = 'live-step-card'
  liveStep.innerHTML = `
    <div class="live-step-label">Current Action</div>
    <div class="live-step-title">${escHtml(focus?.label || 'Waiting')}</div>
    <div class="live-step-detail">${escHtml(focus.detail)}</div>
  `
  card.appendChild(liveStep)

  const focusBlock = document.createElement('div')
  focusBlock.className = 'live-focus-block'
  focusBlock.innerHTML = `
    <div class="artifact-group-label">${escHtml(focus?.focusLabel || 'Current focus')}</div>
  `
  const focusBody = document.createElement('div')
  focusBody.className = 'live-focus-items'
  ;(focus?.items || []).forEach(item => {
    if (item.kind === 'file' && item.path) {
      focusBody.appendChild(makeFileButton(item.path, item.label, 'live-focus-chip live-file-button'))
      return
    }
    const chip = document.createElement('div')
    chip.className = 'live-focus-chip'
    chip.textContent = item.label || ''
    focusBody.appendChild(chip)
  })
  if (!focusBody.childElementCount) {
    const empty = document.createElement('div')
    empty.className = 'artifact-empty'
    empty.textContent = 'No additional context is attached to this live step yet.'
    focusBody.appendChild(empty)
  }
  focusBlock.appendChild(focusBody)
  card.appendChild(focusBlock)
  card.appendChild(renderWorkflowEducationCard(currentAgent))

  board.appendChild(card)
}

function renderRoleFlow (session) {
  const wrap = document.getElementById('role-flow')
  if (!wrap) return
  const agents = session.workflow_process?.agents || []
  wrap.innerHTML = ''

  if (!agents.length) {
    wrap.innerHTML = '<div class="no-active-nodes">No workflow loaded</div>'
    return
  }

  const diagram = document.createElement('div')
  diagram.className = 'role-flow-diagram'

  const byRole = Object.fromEntries(agents.map(agent => [agent.role, agent]))
  const planner = byRole['Task Planner']
  const author = byRole['Patch Author']
  const reviewer = byRole['Code Reviewer']
  const tester = byRole['Test Runner']

  const createChip = (agent, area) => {
    if (!agent) return
    const reference = workflowRoleReference(agent.role)
    const button = document.createElement('button')
    button.type = 'button'
    button.className = `role-flow-chip ${roleToneClass(agent.role)} status-${agent.status} mode-${agent.mode}`.trim()
    button.style.gridArea = area
    const runBadge = agent.run_count > 1
      ? `<span class="role-flow-run-count">↩ ${escHtml(String(agent.run_count))} runs</span>`
      : ''
    button.innerHTML = `
      <span class="role-flow-state">${escHtml(agent.status)}</span>
      <span class="role-flow-name">${escHtml(agent.role)}</span>
      <span class="role-flow-copy">${escHtml(reference.shortLabel)}</span>
      ${runBadge}
    `
    button.addEventListener('click', () => openAgentDetail(agent.role))
    diagram.appendChild(button)
  }

  const createArrow = (symbol, area, extraClass = '') => {
    const arrow = document.createElement('div')
    arrow.className = `role-flow-arrow ${extraClass}`.trim()
    arrow.style.gridArea = area
    arrow.textContent = symbol
    diagram.appendChild(arrow)
  }

  createChip(planner, 'planner')
  createArrow('→', 'arrow-top')
  createChip(author, 'author')
  createArrow('↙', 'arrow-turn', 'role-flow-arrow-turn')
  createChip(reviewer, 'reviewer')
  createArrow('→', 'arrow-bottom')
  createChip(tester, 'tester')

  wrap.appendChild(diagram)
}

function currentInputActivity (session) {
  if (!session) return null
  const current = session.workflow_process?.current_activity || null
  if (current) return current
  const role = inputRoleForSession(session)
  return (session.workflow_process?.agents || []).find(agent => agent.role === role)?.current_activity || null
}

function responseContextArtifactsForRole (role, artifacts) {
  const items = artifacts || []
  const sortByPriority = (values, priority) => [...values].sort((a, b) => {
    const left = priority[a.kind] ?? 99
    const right = priority[b.kind] ?? 99
    return left - right
  })

  if (role === 'Code Reviewer') {
    const prioritized = items.filter(artifact => ['proposal', 'patch', 'files_changed', 'handoff'].includes(artifact.kind))
    return prioritized.length
      ? sortByPriority(prioritized, { patch: 0, files_changed: 1, proposal: 2, handoff: 3 })
      : items
  }
  if (role === 'Test Runner') {
    const prioritized = items.filter(artifact => ['plan_checks', 'proposal', 'patch', 'review', 'review_notes', 'validation_focus', 'test_run', 'handoff'].includes(artifact.kind))
    return prioritized.length
      ? sortByPriority(prioritized, { patch: 0, validation_focus: 1, review: 2, review_notes: 3, plan_checks: 4, test_run: 5, proposal: 6, handoff: 7 })
      : items
  }
  if (role === 'Patch Author') {
    const prioritized = items.filter(artifact => ['handoff', 'issue', 'hint'].includes(artifact.kind))
    return prioritized.length
      ? sortByPriority(prioritized, { handoff: 0, issue: 1, hint: 2 })
      : items
  }
  return items
}

function renderArtifactValue (artifact, className = '') {
  const isCode = artifactNeedsCodeBlock(artifact)
  const content = document.createElement(isCode ? 'pre' : 'div')
  content.className = isCode
    ? `artifact-code-block ${className}`.trim()
    : `artifact-text-block ${className}`.trim()
  content.textContent = artifact?.content || '(not available)'

  if (contentLineCount(artifact?.content || '') <= 3 || !shouldCollapseArtifact(artifact)) {
    return content
  }

  const wrap = document.createElement('div')
  wrap.className = 'artifact-collapse'
  content.classList.add('is-collapsed')

  const button = document.createElement('button')
  button.type = 'button'
  button.className = 'artifact-collapse-toggle'
  button.textContent = 'Expand'
  button.dataset.expanded = 'false'
  button.addEventListener('click', () => {
    const shouldExpand = button.dataset.expanded !== 'true'
    content.classList.toggle('is-collapsed', !shouldExpand)
    button.textContent = shouldExpand ? 'Collapse' : 'Expand'
    button.dataset.expanded = shouldExpand ? 'true' : 'false'
  })

  wrap.appendChild(content)
  wrap.appendChild(button)
  return wrap
}

function renderResponseContext (session) {
  const wrap = document.getElementById('response-context')
  if (!wrap) return

  const waiting = session?.status === 'waiting_for_human' && pendingOperation?.kind !== 'submission'
  const role = inputRoleForSession(session)
  if (!waiting || !['Code Reviewer', 'Test Runner', 'Patch Author'].includes(role)) {
    wrap.style.display = 'none'
    wrap.innerHTML = ''
    return
  }

  const activity = currentInputActivity(session)
  const files = activity?.files_in_scope || []
  const artifacts = responseContextArtifactsForRole(role, activity?.input_artifacts || [])

  // For Patch Author, only show the context panel if there's actually a planner handoff to display
  if (role === 'Patch Author' && !artifacts.length) {
    wrap.style.display = 'none'
    wrap.innerHTML = ''
    return
  }

  const title = role === 'Code Reviewer'
    ? 'Patch to review'
    : role === 'Patch Author'
      ? 'Task Planner input'
      : 'Validation context'
  const note = role === 'Code Reviewer'
    ? 'Inspect the proposed change before deciding whether it is ready to move forward.'
    : role === 'Patch Author'
      ? 'The Task Planner has handed off a plan for this bug. Use it to guide your patch.'
      : 'Use the current patch, review notes, and any runtime evidence to decide whether the fix is proven enough to pass or whether the evidence shows a real defect.'

  wrap.style.display = ''
  wrap.className = `response-context ${roleToneClass(role)}`.trim()
  wrap.innerHTML = `
    <div class="response-context-head">
      <div class="response-context-title">${escHtml(title)}</div>
      <div class="response-context-note">${escHtml(note)}</div>
    </div>
  `

  if (files.length) {
    const filesCard = document.createElement('section')
    filesCard.className = 'response-context-card'
    filesCard.innerHTML = '<div class="response-context-label">Files in focus</div>'
    const fileList = document.createElement('div')
    fileList.className = 'response-context-files'
    files.forEach(file => {
      fileList.appendChild(makeFileButton(file.path || file.label, file.label || shortPathLabel(file.path), 'response-context-file'))
    })
    filesCard.appendChild(fileList)
    wrap.appendChild(filesCard)
  }

  artifacts.forEach(artifact => {
    const card = document.createElement('section')
    card.className = 'response-context-card'
    card.innerHTML = `
      <div class="response-context-label">${escHtml(artifact.title || artifact.kind || 'Artifact')}</div>
      ${artifact.origin ? `<div class="response-context-origin">${escHtml(artifact.origin)}</div>` : ''}
    `
    card.appendChild(renderArtifactValue(artifact))
    wrap.appendChild(card)
  })
}

function renderModalArtifactBlock (title, artifact) {
  const block = document.createElement('div')
  block.className = 'agent-detail-section'
  block.innerHTML = `<div class="agent-detail-section-label">${escHtml(title)}</div>`
  block.appendChild(renderArtifactValue(artifact, 'agent-detail-pre'))
  return block
}

function renderWorkflowArrow (symbol = '↓', className = '') {
  const arrow = document.createElement('div')
  arrow.className = `agent-flow-arrow ${className}`.trim()
  arrow.textContent = symbol
  return arrow
}

function renderPacketArtifacts (artifacts, emptyText) {
  const body = document.createElement('div')
  body.className = 'agent-packet-body'
  if (!artifacts.length) {
    const empty = document.createElement('div')
    empty.className = 'artifact-empty'
    empty.textContent = emptyText
    body.appendChild(empty)
    return body
  }

  artifacts.forEach(artifact => {
    const card = document.createElement('div')
    card.className = 'agent-packet-artifact'
    card.innerHTML = `
      <div class="agent-packet-artifact-head">
        <div class="agent-detail-section-label">${escHtml(artifact.title || artifact.kind || 'Artifact')}</div>
        ${artifact.origin ? `<div class="agent-packet-origin">${escHtml(artifact.origin)}</div>` : ''}
      </div>
    `
    if (Array.isArray(artifact.paths) && artifact.paths.length) {
      const files = document.createElement('div')
      files.className = 'agent-packet-file-list'
      artifact.paths.forEach(path => {
        files.appendChild(makeFileButton(path, shortPathLabel(path), 'agent-detail-file'))
      })
      card.appendChild(files)
    } else {
      card.appendChild(renderArtifactValue(artifact))
    }
    body.appendChild(card)
  })
  return body
}

function renderWorkflowPacket (title, subtitle, artifacts, options = {}) {
  const card = document.createElement('section')
  card.className = `agent-flow-packet ${options.className || ''}`.trim()
  card.innerHTML = `
    <div class="agent-flow-packet-title">${escHtml(title)}</div>
    <div class="agent-flow-packet-subtitle">${escHtml(subtitle)}</div>
  `
  card.appendChild(renderPacketArtifacts(artifacts, options.emptyText || 'No packet details recorded.'))
  return card
}

function renderSequenceFlow (sequence) {
  const flow = document.createElement('div')
  flow.className = 'agent-process-flow'

  ;(sequence || []).forEach((item, idx) => {
    const step = document.createElement('section')
    step.className = 'agent-process-step'
    step.innerHTML = `
      <div class="agent-flow-packet-title">${escHtml(item.label || 'Step')}</div>
      <div class="agent-process-copy">${escHtml(item.detail || '')}</div>
    `
    flow.appendChild(step)
    if (idx < (sequence || []).length - 1) {
      flow.appendChild(renderWorkflowArrow('↓', 'agent-process-arrow'))
    }
  })

  if (!flow.childElementCount) {
    const empty = document.createElement('div')
    empty.className = 'artifact-empty'
    empty.textContent = 'No workflow steps recorded.'
    flow.appendChild(empty)
  }

  return flow
}

function renderModalFilesBlock (files) {
  const block = document.createElement('div')
  block.className = 'agent-detail-section'
  const label = document.createElement('div')
  label.className = 'agent-detail-section-label'
  label.textContent = 'Files In Scope'
  block.appendChild(label)

  const list = document.createElement('div')
  list.className = 'agent-detail-file-list'
  const items = (files || []).filter(file => file?.path || file?.label)
  if (!items.length) {
    const empty = document.createElement('span')
    empty.className = 'artifact-empty'
    empty.textContent = 'No files recorded.'
    list.appendChild(empty)
  } else {
    items.forEach(file => {
      list.appendChild(makeFileButton(file.path || file.label, file.label || shortPathLabel(file.path), 'agent-detail-file'))
    })
  }
  block.appendChild(list)
  return block
}

function renderAgentRunDetailModal (run) {
  const wrap = document.createElement('div')
  wrap.className = 'agent-detail-run'
  const meta = [
    `Turn ${run.turn}`,
    run.actor === 'human' ? 'You' : 'System',
    fmtTime(run.completed_at || run.started_at),
    fmtDuration(run.duration_seconds)
  ].filter(Boolean).join(' · ')

  wrap.innerHTML = `<div class="agent-detail-run-meta">${escHtml(meta)}</div>`

  const flow = document.createElement('div')
  flow.className = 'agent-detail-flow'

  const inputArtifacts = [
    ...(run.prompt_artifact ? [run.prompt_artifact] : []),
    ...(run.input_artifacts || []),
    ...((run.files_in_scope || []).length
      ? [{
          title: 'Files in scope',
          kind: 'files',
          content: '',
          origin: 'Repository context',
          paths: (run.files_in_scope || []).map(file => file.path || file.label).filter(Boolean)
        }]
      : [])
  ]
  flow.appendChild(renderWorkflowPacket(
    'Input Packet',
    'What this agent received, including its prompt and the concrete context it could use.',
    inputArtifacts,
    { className: 'packet-input', emptyText: 'No recorded input packet.' }
  ))
  flow.appendChild(renderWorkflowArrow())

  const processCard = document.createElement('section')
  processCard.className = 'agent-flow-packet packet-process'
  processCard.innerHTML = `
    <div class="agent-flow-packet-title">Processing Steps</div>
    <div class="agent-flow-packet-subtitle">How this agent moved from input context to a decision or artifact.</div>
  `
  processCard.appendChild(renderSequenceFlow(run.sequence || []))
  flow.appendChild(processCard)
  flow.appendChild(renderWorkflowArrow())

  const outputArtifacts = [
    ...(run.output_artifacts || []),
    ...(run.handoff?.prompt_artifact
      ? [{
          ...run.handoff.prompt_artifact,
          title: `Handoff Packet to ${run.handoff.to_role}`
        }]
      : [])
  ]
  flow.appendChild(renderWorkflowPacket(
    'Output / Handoff Packet',
    'What this agent produced and passed forward to the next stage.',
    outputArtifacts,
    { className: 'packet-output', emptyText: 'No output packet recorded.' }
  ))

  wrap.appendChild(flow)
  return wrap
}

function renderAgentActivityDetailModal (agent, activity) {
  const wrap = document.createElement('div')
  wrap.className = 'agent-detail-run'

  const statusBits = [
    'Current state',
    agent?.mode === 'manual' ? 'Your step' : 'System step',
    agent?.status === 'current' ? 'Awaiting output' : ''
  ].filter(Boolean)
  wrap.innerHTML = `<div class="agent-detail-run-meta">${escHtml(statusBits.join(' · '))}</div>`

  const flow = document.createElement('div')
  flow.className = 'agent-detail-flow'

  const inputArtifacts = [
    ...(activity?.prompt_artifact ? [activity.prompt_artifact] : []),
    ...(activity?.input_artifacts || []),
    ...((activity?.files_in_scope || []).length
      ? [{
          title: 'Files in scope',
          kind: 'files',
          content: '',
          origin: 'Repository context',
          paths: (activity.files_in_scope || []).map(file => file.path || file.label).filter(Boolean)
        }]
      : [])
  ]
  flow.appendChild(renderWorkflowPacket(
    'Current Input Packet',
    'What this role would use right now if you opened its live context.',
    inputArtifacts,
    { className: 'packet-input', emptyText: 'No current input packet is available yet.' }
  ))
  flow.appendChild(renderWorkflowArrow())

  const processCard = document.createElement('section')
  processCard.className = 'agent-flow-packet packet-process'
  processCard.innerHTML = `
    <div class="agent-flow-packet-title">Current Process</div>
    <div class="agent-flow-packet-subtitle">The structured steps this role is expected to follow before producing an output.</div>
  `
  processCard.appendChild(renderSequenceFlow(activity?.sequence || []))
  flow.appendChild(processCard)
  flow.appendChild(renderWorkflowArrow())

  const outputArtifacts = [
    ...(activity?.output_artifacts || []),
    ...(activity?.handoff?.prompt_artifact
      ? [{
          ...activity.handoff.prompt_artifact,
          title: `Handoff Packet to ${activity.handoff.to_role}`
        }]
      : [])
  ]
  flow.appendChild(renderWorkflowPacket(
    'Expected Output / Handoff Packet',
    'What this role will emit once it finishes the current step.',
    outputArtifacts,
    {
      className: 'packet-output',
      emptyText: 'No output is recorded yet. The structured response and any handoff will appear here after this step finishes.'
    }
  ))

  wrap.appendChild(flow)
  return wrap
}

function closeAgentDetail () {
  const modal = document.getElementById('agent-detail-modal')
  if (modal) modal.style.display = 'none'
}

function closeFileViewer () {
  const modal = document.getElementById('file-viewer-modal')
  if (modal) modal.style.display = 'none'
}

function makeFileButton (path, label, className = 'agent-detail-file') {
  const button = document.createElement('button')
  button.type = 'button'
  button.className = className
  button.textContent = label || shortPathLabel(path)
  button.title = path
  button.addEventListener('click', () => openFileViewer(path))
  return button
}

async function openFileViewer (path) {
  if (!path || !currentSessionId) return
  const modal = document.getElementById('file-viewer-modal')
  const title = document.getElementById('file-viewer-title')
  const meta = document.getElementById('file-viewer-meta')
  const content = document.getElementById('file-viewer-content')
  if (!modal || !title || !meta || !content) return

  title.textContent = shortPathLabel(path)
  meta.textContent = path
  content.textContent = 'Loading file...'
  modal.style.display = ''

  if (fileViewerCache[path] !== undefined) {
    content.textContent = fileViewerCache[path]
    return
  }

  try {
    const data = await api(`/api/sessions/${currentSessionId}/repo/file?path=${encodeURIComponent(path)}`)
    const loaded = data.content || '(empty file)'
    fileViewerCache = {
      ...fileViewerCache,
      [path]: loaded
    }
    content.textContent = loaded
  } catch (err) {
    const failed = `Unable to load file.\n\n${String(err)}`
    fileViewerCache = {
      ...fileViewerCache,
      [path]: failed
    }
    content.textContent = failed
  }
}

function openAgentDetail (role) {
  const modal = document.getElementById('agent-detail-modal')
  const title = document.getElementById('agent-detail-title')
  const meta = document.getElementById('agent-detail-meta')
  const body = document.getElementById('agent-detail-body')
  if (!modal || !title || !meta || !body || !currentSession) return

  const agents = currentSession.workflow_process?.agents || []
  const agent = agents.find(item => item.role === role)
  if (!agent) return

  title.textContent = `${agent.role} detailed workflow`
  meta.textContent = `${agent.mode === 'manual' ? 'Your role in this session' : 'System-owned role'} · ${agent.run_count || 0} recorded run${agent.run_count === 1 ? '' : 's'}`
  body.innerHTML = ''
  const reference = workflowRoleReference(agent.role)

  const intro = document.createElement('div')
  intro.className = 'agent-detail-intro'
  intro.innerHTML = `
    <div class="agent-detail-section-label">How this agent works</div>
    <div class="agent-detail-copy">${escHtml(agent.ai_instruction || '')}</div>
    <div class="agent-detail-copy">${escHtml(agent.why_needed || '')}</div>
    <div class="agent-detail-copy">${escHtml(agent.how_to_do_well || '')}</div>
  `
  const guideGrid = document.createElement('div')
  guideGrid.className = 'agent-detail-guide-grid'
  ;[
    { label: 'Receives', text: reference.receives.join(' · ') },
    { label: 'Produces', text: reference.produces.join(' · ') },
    { label: 'Why the handoff matters', text: reference.handoffPurpose }
  ].forEach(item => {
    const card = document.createElement('div')
    card.className = 'agent-detail-guide-card'
    card.innerHTML = `
      <div class="agent-detail-section-label">${escHtml(item.label)}</div>
      <div class="agent-detail-copy">${escHtml(item.text || 'Not recorded')}</div>
    `
    guideGrid.appendChild(card)
  })
  intro.appendChild(guideGrid)
  body.appendChild(intro)

  if (agent.current_activity) {
    body.appendChild(renderAgentActivityDetailModal(agent, agent.current_activity))
  }

  if (agent.runs?.length) {
    agent.runs.forEach(run => {
      body.appendChild(renderAgentRunDetailModal(run))
    })
  } else if (!agent.current_activity) {
    const empty = document.createElement('div')
    empty.className = 'agent-detail-run'
    empty.innerHTML = `
      <div class="agent-detail-run-meta">No completed run yet</div>
      <div class="agent-detail-copy">This agent has not executed yet in the current workflow. When it does, this panel will show its full input → process → output → handoff path.</div>
    `
    body.appendChild(empty)
  }

  modal.style.display = ''
}

function renderTaskContext (session) {
  const task = session.task || {}
  const changedFiles = (task.changed_files || []).slice(0, 8).join(', ') || '(not available)'
  const difficulty = task.educational_fit?.difficulty_label || task.educational_fit?.difficulty_band || '(not labeled)'
  const summary = buildIssueSummary(task)
  const fixDirection = task.task_focus || task.issue_summary || task.problem_statement || '(not available)'
  const validationTarget = task.suggested_test_commands?.[0] || task.target_test_file || '(not specified)'

  setText(
    'task-summary',
    `Repository: ${task.repo || ''}\n` +
    `Issue summary:\n${summary}\n\n` +
    `Practice focus:\n${fixDirection}\n\n` +
    `Difficulty: ${difficulty}\n` +
    `Relevant files: ${changedFiles}\n` +
    `Validation target: ${validationTarget}\n\n` +
    `Issue details:\n${task.problem_statement || ''}\n\n` +
    `Hints:\n${task.hints_text || '(none)'}`
  )
}

function updateInputState (session) {
  const waiting = session.status === 'waiting_for_human' && pendingOperation?.kind !== 'submission'
  const textarea = document.getElementById('task-input')
  const sendBtn = document.getElementById('btn-send')
  const aiBtn = document.getElementById('btn-ai-draft')
  const launchBtn = document.getElementById('btn-launch')
  const inputArea = document.getElementById('input-area')
  const leftPanel = document.getElementById('workflow-left-panel')
  const role = inputRoleForSession(session)

  if (waiting) {
    syncInputDraft(session)
    setText('input-role-title', `${role} Response`)
    setText(
      'input-role-note',
      'Edit the scaffold below, review the live workflow and role details if needed, then click Send when ready.'
    )
  }

  if (inputArea) inputArea.style.display = waiting ? '' : 'none'
  if (leftPanel) leftPanel.classList.toggle('input-hidden', !waiting)

  if (textarea) {
    textarea.disabled = false
    textarea.placeholder = waiting
      ? `Edit the ${role} scaffold and send when you are ready.`
      : ''
  }
  if (sendBtn) sendBtn.disabled = !waiting
  if (aiBtn) aiBtn.disabled = !waiting || Boolean(pendingOperation?.active)
  if (launchBtn) {
    launchBtn.style.display = 'none'
    launchBtn.disabled = true
  }

  renderResponseContext(session)
}

function renderSession (session) {
  currentSessionId = session.session_id
  currentSession = session
  recordPracticeCompletionIfNeeded(session)
  renderParticipantBanner(session)
  renderStatusDisplay(session)
  renderTaskContext(session)
  renderRoleFlow(session)
  renderWorkflowBoard(session)
  updateInputState(session)
}

function startPendingOperation (kind, session) {
  const systemStatus = session?.system_status || {}
  const currentRole = session?.workflow_process?.current_activity?.role || session?.current_step?.role || systemStatus.current_role || session?.manual_step_role || 'Workflow'
  let label = `Running ${currentRole}...`
  let detail = 'The system is executing the next workflow step.'

  if (kind === 'submission') {
    label = `Processing your ${session?.waiting_role || session?.manual_step_role || 'response'}...`
    detail = 'Your response is being recorded, routed, and turned into the next workflow artifact.'
  } else if (currentRole === 'Code Reviewer') {
    detail = 'The reviewer is inspecting the patch, weighing evidence, and deciding whether to approve it for testing or send it back for revision.'
  } else if (currentRole === 'Test Runner') {
    detail = 'The tester is checking the available validation evidence and deciding whether the workflow should finish or return to Patch Author because the evidence shows a defect.'
  } else if (currentRole === 'Patch Author') {
    detail = 'The patch author is turning the current plan or feedback into a concrete change proposal.'
  } else if (currentRole === 'Task Planner') {
    detail = 'The planner is organizing the issue into a focused implementation path.'
  }

  pendingOperation = {
    active: true,
    kind,
    startedAt: Date.now(),
    role: currentRole,
    detail,
    systemStatus: {
      ...systemStatus,
      label
    }
  }

  if (pendingTimerId) window.clearInterval(pendingTimerId)
  pendingTimerId = window.setInterval(() => {
    if (currentSession) {
      renderStatusDisplay(currentSession)
      renderWorkflowBoard(currentSession)
    }
  }, 1000)
  if (currentSession) {
    renderStatusDisplay(currentSession)
    renderWorkflowBoard(currentSession)
  }
}

function stopPendingOperation () {
  pendingOperation = null
  if (pendingTimerId) {
    window.clearInterval(pendingTimerId)
    pendingTimerId = null
  }
  if (currentSession) {
    renderStatusDisplay(currentSession)
    renderWorkflowBoard(currentSession)
  }
}

async function autoAdvanceWorkflow (session) {
  let latestSession = session || currentSession
  if (!shouldAutoAdvance(latestSession) || autoRunInFlight) return

  autoRunInFlight = true
  try {
    while (shouldAutoAdvance(latestSession)) {
      startPendingOperation('workflow', latestSession)
      await sleep(1200)
      const data = await api(`/api/sessions/${currentSessionId}/start`, { method: 'POST' })
      const completedRole = pendingOperation?.role
      stopPendingOperation()
      latestSession = data.session
      renderSession(latestSession)

      const completionFlash = buildCompletionFlash(latestSession, completedRole)
      if (completionFlash) {
        liveCompletionFlash = completionFlash
        renderWorkflowBoard(latestSession)
        await sleep(1400)
        liveCompletionFlash = null
        renderWorkflowBoard(latestSession)
      }

      if (shouldAutoAdvance(latestSession)) await sleep(500)
    }
    if (latestSession) renderSession(latestSession)
  } catch (err) {
    stopPendingOperation()
    addNotification(String(err), 'warning')
    await refreshSession()
  } finally {
    autoRunInFlight = false
  }
}

async function runWorkflow () {
  if (!currentSessionId) return
  await autoAdvanceWorkflow(currentSession)
}

async function submitHumanTurn () {
  if (!currentSessionId) return
  const textarea = document.getElementById('task-input')
  cacheCurrentDraft()
  const text = (textarea ? textarea.value : '').trim()
  if (!text) return

  startPendingOperation('submission', currentSession)
  try {
    const data = await api(`/api/sessions/${currentSessionId}/human-input`, {
      method: 'POST',
      body: JSON.stringify({ text })
    })
    resetDraftForSession(currentSession)
    renderSession(data.session)
    if (shouldAutoAdvance(data.session)) {
      await autoAdvanceWorkflow(data.session)
    } else {
      stopPendingOperation()
      if (textarea) textarea.value = ''
      renderSession(data.session)
    }
  } catch (err) {
    stopPendingOperation()
    addNotification(String(err), 'warning')
    await refreshSession()
  }
}

async function handleSendButton () {
  if (currentSession && currentSession.status === 'waiting_for_human') {
    await submitHumanTurn()
  } else {
    await runWorkflow()
  }
}

async function requestAIDraft () {
  if (!currentSessionId) return
  const textarea = document.getElementById('task-input')
  const aiBtn = document.getElementById('btn-ai-draft')
  cacheCurrentDraft()
  const question = (textarea && textarea.value.trim()) || 'Help me draft the best response for my current step.'
  if (aiBtn) aiBtn.disabled = true

  try {
    const data = await api(`/api/sessions/${currentSessionId}/support`, {
      method: 'POST',
      body: JSON.stringify({ question })
    })
    if (textarea) {
      setDraftText(currentSession, data.support || '')
      textarea.focus()
      textarea.setSelectionRange(textarea.value.length, textarea.value.length)
    }
    addNotification('AI Coach draft is ready in the response box. Review it, edit it, and send it manually if you want to use it.')
  } catch (err) {
    addNotification(String(err), 'warning')
  } finally {
    if (aiBtn) aiBtn.disabled = currentSession?.status !== 'waiting_for_human' || Boolean(pendingOperation?.active)
  }
}

async function refreshSession () {
  if (!currentSessionId) return
  try {
    const data = await api(`/api/sessions/${currentSessionId}`)
    renderSession(data.session)
  } catch (err) {
    console.error(err)
  }
}

function addNotification (message, type = 'notification') {
  const wrap = document.getElementById('inline-notices')
  if (!wrap) return
  const el = document.createElement('div')
  el.className = `inline-notice${type === 'warning' ? ' inline-notice-warning' : ''}`
  el.innerHTML = `
    <div class="inline-notice-text">${escHtml(message)}</div>
  `
  wrap.appendChild(el)
}

function returnToStudyHub () {
  pendingOperation = null
  autoRunInFlight = false
  if (pendingTimerId) {
    window.clearInterval(pendingTimerId)
    pendingTimerId = null
  }
  currentSessionId = null
  currentSession = null
  liveCompletionFlash = null
  fileViewerCache = {}
  clearInlineNotices()
  closeAgentDetail()
  closeFileViewer()
  syncPracticeCompletion()
  showStudyHub()
  renderHubTaskProgress()
  previewEasiest()
}

function handleEnterKey (event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    handleSendButton()
  }
}

function bindEvents () {
  const participantInput = document.getElementById('hub-participant-name')
  if (participantInput) {
    participantInput.addEventListener('input', () => {
      syncPracticeCompletion(participantInput.value)
      renderHubTaskProgress()
    })
  }

  const starts = [
    ['hub-start-btn-1', 'planner', 1],
    ['hub-start-btn-2', 'coder', 2],
    ['hub-start-btn-3', 'reviewer', 3],
    ['hub-start-btn-4', 'tester', 4]
  ]
  starts.forEach(([id, manualStep, idx]) => {
    const btn = document.getElementById(id)
    if (btn) btn.onclick = () => openTask(manualStep, idx)
  })

  const launchBtn = document.getElementById('btn-launch')
  if (launchBtn) launchBtn.onclick = runWorkflow

  const sendBtn = document.getElementById('btn-send')
  if (sendBtn) sendBtn.onclick = handleSendButton

  const aiDraftBtn = document.getElementById('btn-ai-draft')
  if (aiDraftBtn) aiDraftBtn.onclick = requestAIDraft

  const hubBtn = document.getElementById('btn-return-hub')
  if (hubBtn) hubBtn.onclick = returnToStudyHub
  const hubBtn2 = document.getElementById('btn-return-hub-2')
  if (hubBtn2) hubBtn2.onclick = returnToStudyHub

  const detailClose = document.getElementById('agent-detail-close')
  if (detailClose) detailClose.onclick = closeAgentDetail
  const detailBackdrop = document.getElementById('agent-detail-backdrop')
  if (detailBackdrop) detailBackdrop.onclick = closeAgentDetail
  const fileClose = document.getElementById('file-viewer-close')
  if (fileClose) fileClose.onclick = closeFileViewer
  const fileBackdrop = document.getElementById('file-viewer-backdrop')
  if (fileBackdrop) fileBackdrop.onclick = closeFileViewer

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      closeAgentDetail()
      closeFileViewer()
    }
  })

  const textarea = document.getElementById('task-input')
  if (textarea) {
    textarea.addEventListener('keydown', handleEnterKey)
    textarea.addEventListener('input', cacheCurrentDraft)
  }
}

async function init () {
  try {
    window.localStorage.removeItem('workflow-practice-completion')
  } catch (err) {}
  bindEvents()
  const participantInput = document.getElementById('hub-participant-name')
  if (participantInput) participantInput.value = ''
  syncPracticeCompletion()
  showStudyHub()
  renderHubTaskProgress()
  await previewEasiest()
}

init()
