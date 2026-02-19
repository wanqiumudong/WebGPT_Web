import Container from '../components/container/container';
import LeftSider from '../components/leftPanel/index';
import { History } from '../components/history/history';
import './index.css';
import styled from "styled-components";
import { useSelector } from 'react-redux';
import { useState, useEffect } from 'react';

const MainContainer = styled.div`
  display: flex;
  position: relative;
`;

function Layout() {
  const mainPage = useSelector((state) => state.PageState.Main_Page);
  const subPage = useSelector((state) => state.PageState.Sub_Page);
  const [modelId, setModelId] = useState(1);

  useEffect(() => {
    switch (subPage) {
      case 'issue':
        setModelId(1);
        break;
      case 'lithgraphy':
        setModelId(2);
        break;
      case 'tcad':
        setModelId(3);
        break;
      case 'spice':
        setModelId(4);
        break;
      case 'circuit':
        setModelId(5);
        break;
      case 'ChatBot':
        setModelId(0);
        break;
      default:
        setModelId(0);
    }
  }, [subPage]);

  return (
    <MainContainer className='layout'>
      <LeftSider />
      <Container />
      {mainPage !== 'ClassIntroduce' && mainPage !== 'RagManager' && <History modelId={modelId} />}
    </MainContainer>
  );
}

export default Layout;
