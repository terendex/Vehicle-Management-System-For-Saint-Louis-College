import api from './axios'

export const violationsApi = {
  getMyViolations: async () => {
    const { data } = await api.get('/violations/my/')
    return data
  },
}
