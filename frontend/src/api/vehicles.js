import api from './axios'

export const getVehicles       = ()     => api.get('/vehicles/')
export const getVehicleProfile = (id)   => api.get(`/vehicles/${id}/profile/`)

export const getRuleConstraints = (params) => api.get('/vehicles/rules/', { params })
export const createRuleConstraint = (data) => api.post('/vehicles/rules/', data)
export const updateRuleConstraint = (id, data) => api.patch(`/vehicles/rules/${id}/`, data)
export const deleteRuleConstraint = (id) => api.delete(`/vehicles/rules/${id}/`)


export const getSystemSettings    = ()     => api.get('/vehicles/system-settings/')
export const updateSystemSettings = (data) => api.put('/vehicles/system-settings/', data)

export const getNotices       = ()        => api.get('/vehicles/notices/')
export const createNotice     = (data)    => api.post('/vehicles/notices/', data)
export const deactivateNotice = (id)      => api.delete(`/vehicles/notices/${id}/`)

export const getRegistrationPeriods       = ()        => api.get('/vehicles/registration-periods/')
export const createRegistrationPeriod     = (data)    => api.post('/vehicles/registration-periods/', data)
export const activateRegistrationPeriod   = (id)      => api.post(`/vehicles/registration-periods/${id}/activate/`)
export const deactivateRegistrationPeriod = (id)      => api.delete(`/vehicles/registration-periods/${id}/activate/`)

export const patchSystemSettings = (data) => api.patch('/vehicles/system-settings/', data)

export const getEvents    = ()           => api.get('/vehicles/events/')
export const createEvent  = (data)       => api.post('/vehicles/events/', data)
export const patchEvent   = (id, data)   => api.patch(`/vehicles/events/${id}/`, data)
export const deleteEvent  = (id)         => api.delete(`/vehicles/events/${id}/`)

export const getSuppliers    = ()           => api.get('/vehicles/suppliers/')
export const createSupplier  = (data)       => api.post('/vehicles/suppliers/', data)
export const patchSupplier   = (id, data)   => api.patch(`/vehicles/suppliers/${id}/`, data)
export const deleteSupplier  = (id)         => api.delete(`/vehicles/suppliers/${id}/`)
export const addSupplierPlate    = (id, data) => api.post(`/vehicles/suppliers/${id}/plates/`, data)
export const deleteSupplierPlate = (id, plateId) => api.delete(`/vehicles/suppliers/${id}/plates/${plateId}/`)
