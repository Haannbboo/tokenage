/** Append each model separately so names are never split or reinterpreted. */
export function appendModels(params: URLSearchParams, models?: string[] | null) {
  models?.forEach(model => params.append('model', model))
}
