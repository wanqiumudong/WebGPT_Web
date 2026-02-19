import { createSlice } from "@reduxjs/toolkit";
// 这里可以切换container容器的显示，点击nav上的图标可以切换显示

const Init_Main_page = "ClassIntroduce";

const initialState = {
    Main_Page: Init_Main_page,
    Sub_Page: "",
    chatMessage: []
}

export const PagaStore = createSlice({
    name: 'PagaStore',
    initialState,
    // 定义 reducers 并生成关联的操作
    reducers: {
        // 定义一个加的方法
        updateMainPage: (state, { payload }) => {
            state.Main_Page = payload.Main_Page
            state.Sub_Page = payload.Sub_Page
        },
        updateChatMessage: (state, { payload }) => {
            state.chatMessage = payload.chatMessage
        }
    }
});

export const {
    updateMainPage,
    updateChatMessage
} = PagaStore.actions

export default PagaStore.reducer