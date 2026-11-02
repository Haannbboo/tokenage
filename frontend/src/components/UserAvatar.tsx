import { useState } from 'react'
import type { AuthUser } from '../types.ts'

export function UserAvatar({ user }: { user: AuthUser }) {
  const [failedPicture, setFailedPicture] = useState<string | null>(null)
  const displayName = user.name || user.email
  const picture = user.picture
  return (
    <span className="user-avatar">
      {picture && picture !== failedPicture ? (
        <img
          src={picture}
          alt=""
          referrerPolicy="no-referrer"
          onError={() => setFailedPicture(picture)}
          style={{ width: '100%', height: '100%', borderRadius: 'inherit', objectFit: 'cover' }}
        />
      ) : displayName.charAt(0).toUpperCase()}
    </span>
  )
}
