import api from './axios'

export const getRuleConstraints = (params) => api.get('/api/vehicles/rules/', { params })
export const createRuleConstraint = (data) => api.post('/api/vehicles/rules/', data)
export const updateRuleConstraint = (id, data) => api.patch(`/api/vehicles/rules/${id}/`, data)
export const deleteRuleConstraint = (id) => api.delete(`/api/vehicles/rules/${id}/`)

export const getVehicleTypeAccess = (params) => api.get('/api/vehicles/vehicle-types/', { params })
export const createVehicleTypeAccess = (data) => api.post('/api/vehicles/vehicle-types/', data)
export const updateVehicleTypeAccess = (id, data) => api.patch(`/api/vehicles/vehicle-types/${id}/`, data)
export const deleteVehicleTypeAccess = (id) => api.delete(`/api/vehicles/vehicle-types/${id}/`)
