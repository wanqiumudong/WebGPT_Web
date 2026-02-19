import { createSlice } from "@reduxjs/toolkit";

const initialState = {
    username: ""
}

export const UserStore = createSlice({
    name: 'UserStore',
    initialState,
    reducers: {
        updateUsername: (state, { payload }) => state.username = payload.username
    }
});

export const {
    updateUsername
} = UserStore.actions

export default UserStore.reducer