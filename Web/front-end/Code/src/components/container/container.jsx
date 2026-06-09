import './container.css';
import { useSelector } from 'react-redux';
import Cookies from 'js-cookie';
import Chatbot from '../../pages/chatBot/index';
import GuangkeBot from '../../pages/guangke/index';
import TCAD from '../../pages/tcad/index';
import ImageBot from '../../pages/fab/fabGPT';
import ClassIntroduce from '../../pages/classIntroduce/index';
import CircuitThink from '../../pages/CircuitThink/index';
import RagManager from '../RagManager';
import { SERVICE_PORTS } from '../../config/endpoints';

function Container() {
  const mainPage = useSelector((state) => state.PageState.Main_Page);
  const subPage = useSelector((state) => state.PageState.Sub_Page);
  const rawUser = Cookies.get('user');
  const normalizedUser = rawUser && rawUser !== 'undefined' && rawUser !== 'null' ? rawUser : '';
  const currentUser = normalizedUser || 'default';

  return (
    <div className='container'>
      <div className='root-container'>
        {mainPage === 'ClassIntroduce' && <ClassIntroduce />}
        {mainPage === 'ChatBot' && subPage === "ChatBot" && <Chatbot port={SERVICE_PORTS.CHATBOT} />}

        {mainPage === 'FabGPT' &&
          <>
            {subPage === "issue" && <ImageBot />}
            {subPage === "lithgraphy" && <GuangkeBot />}
            {subPage === "tcad" && <TCAD port={SERVICE_PORTS.TCAD} />}
            {subPage === "circuit" && <CircuitThink port={SERVICE_PORTS.CIRCUIT} />}
          </>
        }

        {mainPage === 'RagManager' && (
          <RagManager port={SERVICE_PORTS.RAG_MANAGER} userId={currentUser} />
        )}
      </div>
    </div>
  );
}

export default Container;
