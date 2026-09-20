import { requestData } from './http'
import type { User } from '@/types/api'

export const usersApi = {
  updateMe(displayName: string) {
    return requestData<User>({
      method: 'PATCH',
      url: '/users/me',
      data: { display_name: displayName },
    })
  },

  changePassword(currentPassword: string, newPassword: string) {
    return requestData<null>({
      method: 'POST',
      url: '/users/me/password',
      data: {
        current_password: currentPassword,
        new_password: newPassword,
      },
    })
  },
}

