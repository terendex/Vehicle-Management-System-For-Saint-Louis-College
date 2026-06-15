import api from './axios'

export const getRuleConstraints = (params) => api.get('/vehicles/rules/', { params })
export const createRuleConstraint = (data) => api.post('/vehicles/rules/', data)
export const updateRuleConstraint = (id, data) => api.patch(`/vehicles/rules/${id}/`, data)
export const deleteRuleConstraint = (id) => api.delete(`/vehicles/rules/${id}/`)

export const getVehicleTypeAccess = (params) => api.get('/vehicles/vehicle-types/', { params })
export const createVehicleTypeAccess = (data) => api.post('/vehicles/vehicle-types/', data)
export const updateVehicleTypeAccess = (id, data) => api.patch(`/vehicles/vehicle-types/${id}/`, data)
export const deleteVehicleTypeAccess = (id) => api.delete(`/vehicles/vehicle-types/${id}/`)
