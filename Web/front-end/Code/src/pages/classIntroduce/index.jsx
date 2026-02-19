import React from 'react';
import './index.css';
import Card from './components/card';
import { classContent, classIndexList, classList, teachersConfig } from './config';
import Person from './components/person';
import ClassItem from './components/classItem';

const ClassIntroduce = () => {

  const teacherList = () => {
    return (
      <div className='teacher-list'>
        {teachersConfig.map((item, index) =>
          <Person data={item} key={index}></Person>
        )}
      </div>
    );
  };

  const classCalender = () => {
    return (
      <div className='class-list-content'>
        {
          classIndexList.map((value, index) => <ClassItem index={value} content={classList[index]} key={index}></ClassItem>)
        }
      </div>
    )
  }

  return (
    <div className='info'>
      <img className='background-image' alt='' src={require('./assets/background.jpg')}></img>
      <div className='card-list'>
        <Card title="课程介绍" content={classContent}></Card>
        <Card title="授课教师" style={{ marginTop: 80 }} content={teacherList()}></Card>
        <Card title="课程日历" style={{ marginTop: 80 }} content={classCalender()}></Card>
      </div>
    </div>
  )
}

export default ClassIntroduce;