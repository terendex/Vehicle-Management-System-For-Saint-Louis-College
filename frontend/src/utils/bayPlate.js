/**
 * The plate to show on a parking bay, or '' when nobody knows it.
 *
 * A guard's record (`occupant_plate`) wins. Otherwise `occupied_by` — except
 * the detector's placeholder "CAMERA", which says *that* the bay is taken, not
 * by whom, and used to be printed under the bay and looked up as if it were a
 * plate.
 */
export function bayPlate(space) {
  if (!space) return ''
  if (space.occupant_plate) return space.occupant_plate
  return space.occupied_by && space.occupied_by !== 'CAMERA' ? space.occupied_by : ''
}

export default bayPlate
