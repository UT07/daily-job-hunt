import { createContext, useContext, useEffect, useState } from 'react'
import { apiGet } from '../api'
import { useAuth } from '../auth/useAuth'

const ProfileContext = createContext({ profile: null, isLoading: true })

export function ProfileProvider({ children }) {
  const { user } = useAuth()
  const [profile, setProfile] = useState(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    if (!user) {
      setProfile(null)
      setIsLoading(false)
      return
    }
    setIsLoading(true)
    apiGet('/api/profile')
      .then((data) => setProfile(data))
      .catch(() => setProfile(null))
      .finally(() => setIsLoading(false))
  }, [user?.id])

  return (
    <ProfileContext.Provider value={{ profile, isLoading }}>
      {children}
    </ProfileContext.Provider>
  )
}

export function useUserProfile() {
  return useContext(ProfileContext)
}
