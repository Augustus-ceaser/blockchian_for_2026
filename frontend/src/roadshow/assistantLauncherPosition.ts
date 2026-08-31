export const ASSISTANT_LAUNCHER_EDGE_GAP = 12
export const ASSISTANT_LAUNCHER_DRAG_THRESHOLD = 5

export function clampAssistantLauncherBottom(
  bottom: number,
  launcherHeight: number,
  viewportHeight: number,
  edgeGap = ASSISTANT_LAUNCHER_EDGE_GAP,
) {
  const maximum = Math.max(edgeGap, viewportHeight - launcherHeight - edgeGap)
  return Math.min(Math.max(bottom, edgeGap), maximum)
}

export function assistantLauncherBottomForPointer(
  startBottom: number,
  startY: number,
  currentY: number,
  launcherHeight: number,
  viewportHeight: number,
) {
  return clampAssistantLauncherBottom(
    startBottom + startY - currentY,
    launcherHeight,
    viewportHeight,
  )
}

export function isAssistantLauncherDrag(
  startY: number,
  currentY: number,
  threshold = ASSISTANT_LAUNCHER_DRAG_THRESHOLD,
) {
  return Math.abs(currentY - startY) >= threshold
}
